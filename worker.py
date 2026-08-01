"""Background folder watcher and processing worker entrypoint."""

import argparse
import hashlib
import json
import os
import signal
import threading
import time
import uuid
from pathlib import Path

from book_processor import IMAGE_EXTENSIONS, natural_key
from database import DatabaseUnavailable

HEARTBEAT_PATH = Path(os.getenv("WORKER_HEARTBEAT_PATH", "/tmp/bookprocessor-worker-health.json"))
HEARTBEAT_MAX_AGE = float(os.getenv("WORKER_HEARTBEAT_MAX_AGE", "30"))


def folder_names(root):
    if not root.is_dir():
        raise ValueError("Корневая папка не найдена")
    return [path.name for path in sorted((path for path in root.iterdir() if path.is_dir()), key=natural_key)]


def folder_signature(folder):
    digest = hashlib.sha256()
    images = (path for path in folder.iterdir() if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS)
    for path in sorted(images, key=lambda item: item.name):
        stat = path.stat()
        digest.update(f"{path.name}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def run_job(app, job_id, root, selected, models, workers, host, timeout,
            source_path=None, source_signature=None, stop_event=None):
    """Run one job; on shutdown, finish OCR or release it if persistence is unavailable."""
    stop_event = stop_event or threading.Event()
    with app.app_context():
        from app.services import database_url, services
        state, svc = app.extensions["job_state"], services()

        def progress(row, completed, total):
            with state.lock:
                state.jobs[job_id].update(status="processing", completed=completed, total=total)
                state.jobs[job_id]["rows"].append(row)

        def start(total):
            with state.lock:
                state.jobs[job_id].update(status="processing", total=total)

        try:
            with state.lock:
                state.jobs[job_id]["status"] = "processing"
            rows = svc.process_books(root, selected, models, workers, host, timeout,
                                     progress, start, None, svc.internet_search)
            wait_seconds = 2
            while True:
                try:
                    with state.lock:
                        state.jobs[job_id].update(status="saving", error=None)
                    database_job_id = svc.database.save_books(
                        database_url(), rows, source_path or str(root), selected,
                        models, source_signature,
                    )
                    break
                except DatabaseUnavailable as error:
                    if stop_event.is_set():
                        raise RuntimeError("Задание возвращено в очередь: PostgreSQL недоступна при остановке") from error
                    with state.lock:
                        state.jobs[job_id].update(status="waiting_database", error=str(error))
                    stop_event.wait(wait_seconds)
                    wait_seconds = min(wait_seconds * 2, 60)
            with state.lock:
                state.jobs[job_id].update(status="completed", completed=len(rows), total=len(rows),
                                          database_job_id=database_job_id, finished_at=time.time())
            return True
        except Exception as error:
            with state.lock:
                state.jobs[job_id].update(status="failed", error=str(error), finished_at=time.time())
                if source_path and source_signature:
                    state.queued_sources.discard((source_path, source_signature))
                    state.folder_candidates[source_path] = (source_signature, time.time())
            return False


class FolderWorker:
    def __init__(self, app, stop_event=None, heartbeat_path=HEARTBEAT_PATH):
        self.app = app
        self.stop_event = stop_event or threading.Event()
        self.heartbeat_path = Path(heartbeat_path)
        self._heartbeat_lock = threading.Lock()
        self._status = "starting"
        self._current_job = None

    def stop(self, *_args):
        self.stop_event.set()

    def heartbeat(self, status=None, current_job=None):
        with self._heartbeat_lock:
            if status is not None:
                self._status, self._current_job = status, current_job
            data = {"pid": os.getpid(), "updated_at": time.time(), "status": self._status,
                    "current_job": self._current_job}
            temporary = self.heartbeat_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data), encoding="utf-8")
            temporary.replace(self.heartbeat_path)

    def _heartbeat_loop(self):
        while not self.stop_event.wait(5):
            try:
                self.heartbeat()
            except OSError:
                pass

    def run(self):
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name="worker-heartbeat", daemon=True)
        self.heartbeat("idle")
        heartbeat_thread.start()
        with self.app.app_context():
            from app.services import application_settings, database_url, services
            root, state = Path(self.app.config["PHOTOS_ROOT"]), self.app.extensions["job_state"]
            while not self.stop_event.is_set():
                interval = 5.0
                try:
                    settings = application_settings()
                    interval = float(settings["FOLDER_SCAN_INTERVAL"])
                    stable = float(settings["FOLDER_STABLE_SECONDS"])
                    now = time.time()
                    self.heartbeat()
                    for folder in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name):
                        if self.stop_event.is_set():
                            break
                        images = [path for path in folder.iterdir() if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS]
                        if len(images) < 2:
                            continue
                        signature, path = folder_signature(folder), str(folder)
                        previous = state.folder_candidates.get(path)
                        if previous is None or previous[0] != signature:
                            state.folder_candidates[path] = (signature, now)
                            continue
                        key = (path, signature)
                        if now - previous[1] < stable or key in state.queued_sources:
                            continue
                        if services().database.source_was_processed(database_url(), path, signature):
                            state.queued_sources.add(key)
                            continue
                        models = [item.strip() for item in settings["OLLAMA_MODELS"].split(",") if item.strip()]
                        job_id = uuid.uuid4().hex
                        with state.lock:
                            state.jobs[job_id] = {"status": "queued", "completed": 0, "total": 0,
                                "rows": [], "error": None, "database_job_id": None,
                                "started_at": now, "finished_at": None, "folder": folder.name}
                            state.queued_sources.add(key)
                        self.heartbeat("processing", job_id)
                        run_job(self.app, job_id, root, [folder.name], models,
                                int(settings["BOOK_WORKERS"]), settings["OLLAMA_HOST"],
                                float(settings["OLLAMA_TIMEOUT"]), path, signature, self.stop_event)
                        self.heartbeat("stopping" if self.stop_event.is_set() else "idle")
                except (OSError, DatabaseUnavailable, ValueError):
                    self.heartbeat("degraded")
                self.stop_event.wait(interval)
            heartbeat_thread.join(timeout=6)
            self.heartbeat("stopped")


def worker_is_healthy(path=HEARTBEAT_PATH, max_age=HEARTBEAT_MAX_AGE):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data.get("status") in {"idle", "processing"} and time.time() - float(data["updated_at"]) <= max_age
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args(argv)
    if args.health:
        return 0 if worker_is_healthy() else 1
    from app import create_app
    app = create_app({"BOOTSTRAP_ADMIN": False})
    worker = FolderWorker(app)
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
