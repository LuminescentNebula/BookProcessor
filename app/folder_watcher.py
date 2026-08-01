import hashlib
import threading
import time
import uuid
from pathlib import Path
from flask import current_app
from database import DatabaseUnavailable
from book_processor import IMAGE_EXTENSIONS, natural_key
from .jobs import run_job
from .services import application_settings, database_url, services

def folder_names(root):
    if not root.is_dir():
        raise ValueError("Корневая папка не найдена")
    directories = sorted((path for path in root.iterdir() if path.is_dir()), key=natural_key)
    return [path.name for path in directories]

def folder_signature(folder):
    digest = hashlib.sha256()
    for path in sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() in IMAGE_EXTENSIONS), key=lambda p: p.name):
        stat = path.stat(); digest.update(f"{path.name}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()

def watch_folders(app, stop_event=None):
    stop_event = stop_event or threading.Event()
    with app.app_context():
        root, state = Path(app.config["PHOTOS_ROOT"]), app.extensions["job_state"]
        while not stop_event.is_set():
            interval = 5
            try:
                settings = application_settings(); interval = float(settings["FOLDER_SCAN_INTERVAL"]); stable = float(settings["FOLDER_STABLE_SECONDS"]); now = time.time()
                for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name):
                    if sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.casefold() in IMAGE_EXTENSIONS) < 2: continue
                    signature, path = folder_signature(folder), str(folder)
                    previous = state.folder_candidates.get(path)
                    if previous is None or previous[0] != signature:
                        state.folder_candidates[path] = (signature, now); continue
                    key = (path, signature)
                    if now - previous[1] < stable or key in state.queued_sources: continue
                    if services().database.source_was_processed(database_url(), path, signature):
                        state.queued_sources.add(key); continue
                    models = [item.strip() for item in settings["OLLAMA_MODELS"].split(",") if item.strip()]
                    job_id = uuid.uuid4().hex
                    with state.lock:
                        state.jobs[job_id] = {"status": "queued", "completed": 0, "total": 0, "rows": [], "error": None, "database_job_id": None, "started_at": now, "finished_at": None, "folder": folder.name}
                        state.queued_sources.add(key)
                    state.executor.submit(run_job, app, job_id, root, [folder.name], models, int(settings["BOOK_WORKERS"]), settings["OLLAMA_HOST"], float(settings["OLLAMA_TIMEOUT"]), path, signature)
            except (OSError, DatabaseUnavailable):
                pass
            stop_event.wait(interval)

def start_folder_watcher(app):
    thread = threading.Thread(target=watch_folders, args=(app,), name="folder-watcher", daemon=True)
    thread.start(); return thread
