#!/usr/bin/env python3
"""Small local web interface for BookProcessor."""

from __future__ import annotations

import os
import threading
import time
import uuid
import hashlib
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

from book_processor import IMAGE_EXTENSIONS, natural_key, process_books
from database import DatabaseUnavailable, book_image_path, check_database, known_book_values, list_books, save_books, source_was_processed

app = Flask(__name__)
app.config["OLLAMA_HOST"] = os.getenv("OLLAMA_HOST", "http://localhost:11434")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
job_executor = ThreadPoolExecutor(max_workers=int(os.getenv("WEB_JOB_WORKERS", "2")))
queued_sources: set[tuple[str, str]] = set()
folder_candidates: dict[str, tuple[str, float]] = {}


def folder_names(root: Path) -> list[str]:
    if not root.is_dir():
        raise ValueError("Корневая папка не найдена")
    return [path.name for path in sorted((p for p in root.iterdir() if p.is_dir()), key=natural_key)]


@app.get("/")
def index():
    return render_template("index.html", ollama_host=app.config["OLLAMA_HOST"])


def database_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor")


def book_page(template: str):
    filters = {name: request.args.get(name, "") for name in (
        "author", "title", "publisher", "genre", "author_select", "publisher_select", "genre_select",
    )}
    current_sort = request.args.get("sort", "id")
    current_direction = request.args.get("direction", "desc")
    sort_urls = {}
    for column in ("box", "author", "title", "year", "publisher", "isbn", "genre"):
        arguments = {key: value for key, value in filters.items() if value}
        arguments.update(sort=column, direction="desc" if current_sort == column and current_direction == "asc" else "asc")
        sort_urls[column] = request.path + "?" + urllib.parse.urlencode(arguments)
    try:
        return render_template(
            template,
            books=list_books(database_url(), filters, current_sort, current_direction),
            known=known_book_values(database_url()), filters=filters,
            sort=current_sort, direction=current_direction, sort_urls=sort_urls,
        )
    except DatabaseUnavailable as error:
        return render_template(
            template, books=[], known={"author": [], "publisher": [], "genre": []},
            filters=filters, error=str(error), sort=current_sort,
            direction=current_direction, sort_urls=sort_urls,
        ), 503


@app.get("/library")
def library():
    return book_page("library.html")


@app.get("/books")
def books_table():
    return book_page("books.html")


@app.get("/api/books/<int:book_id>/image/<image_kind>")
def book_image(book_id: int, image_kind: str):
    if image_kind not in {"cover", "info"}:
        abort(404)
    path = book_image_path(database_url(), book_id, image_kind)
    if path is None or not path.is_file():
        abort(404)
    return send_file(path)


@app.get("/api/folders")
def folders():
    try:
        return jsonify({"folders": folder_names(Path(request.args.get("root", "")))})
    except (OSError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


def check_ollama(host: str) -> tuple[bool, str]:
    """Perform a short Ollama API readiness check."""
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=3) as response:
            json.load(response)
        return True, "Ollama доступна"
    except (OSError, ValueError, urllib.error.URLError) as error:
        return False, f"Ollama недоступна: {error}"


@app.get("/api/health")
def health():
    database_ok, database_message = check_database(os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor"))
    ollama_ok, ollama_message = check_ollama(app.config["OLLAMA_HOST"])
    photos_root = Path(os.getenv("PHOTOS_ROOT", "/photos"))
    photos_ok = photos_root.is_dir()
    photos_message = f"Каталог фотографий: {photos_root}" if photos_ok else f"Mounted-каталог не найден: {photos_root}"
    return jsonify({
        "status": "ready" if database_ok and ollama_ok and photos_ok else "degraded",
        "database": {"ok": database_ok, "message": database_message},
        "ollama": {"ok": ollama_ok, "message": ollama_message},
        "photos": {"ok": photos_ok, "message": photos_message},
    }), 200


@app.post("/process")
def process():
    try:
        root = Path(request.form["root"]).expanduser()
        models = [item.strip() for item in request.form["models"].split(",") if item.strip()]
        selected = request.form.getlist("folders") or None
        workers = int(request.form["workers"])
        timeout = float(request.form["timeout"])
        if workers < 1 or timeout <= 0 or not models or not root.is_dir():
            raise ValueError("Проверьте папку, модели, количество потоков и тайм-аут")
        job_id = uuid.uuid4().hex
        with jobs_lock:
            jobs[job_id] = {
                "status": "queued", "completed": 0, "total": 0, "rows": [],
                "error": None, "database_job_id": None, "started_at": time.time(),
                "finished_at": None,
            }
        job_executor.submit(run_job, job_id, root, selected, models, workers, request.form["ollama_host"], timeout)
        return jsonify({"job_id": job_id}), 202
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    with jobs_lock:
        state = jobs.get(job_id)
        if state is None:
            return jsonify({"error": "Обработка не найдена"}), 404
        end = state["finished_at"] or time.time()
        elapsed_seconds = max(0, int(end - state["started_at"]))
        return jsonify({
            **state,
            "elapsed_seconds": elapsed_seconds,
            "rows": [dict(row) for row in state["rows"]],
        })


@app.get("/api/jobs")
def all_jobs():
    with jobs_lock:
        result = []
        for job_id, state in reversed(list(jobs.items())):
            end = state["finished_at"] or time.time()
            result.append({"job_id": job_id, **state, "elapsed_seconds": max(0, int(end - state["started_at"]))})
        return jsonify({"jobs": result})


def folder_signature(folder: Path) -> str:
    """Build a stable signature that changes when photos are added or replaced."""
    digest = hashlib.sha256()
    for path in sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() in IMAGE_EXTENSIONS), key=lambda p: p.name):
        stat = path.stat()
        digest.update(f"{path.name}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def watch_folders() -> None:
    """Continuously enqueue stable folders found in the mounted photo root."""
    root = Path(os.getenv("PHOTOS_ROOT", "/photos"))
    interval = float(os.getenv("FOLDER_SCAN_INTERVAL", "5"))
    stable_seconds = float(os.getenv("FOLDER_STABLE_SECONDS", "10"))
    database_url = os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor")
    while True:
        try:
            now = time.time()
            for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name):
                if sum(1 for path in folder.iterdir() if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS) < 2:
                    continue
                signature = folder_signature(folder)
                previous = folder_candidates.get(str(folder))
                if previous is None or previous[0] != signature:
                    folder_candidates[str(folder)] = (signature, now)
                    continue
                key = (str(folder), signature)
                if now - previous[1] < stable_seconds or key in queued_sources:
                    continue
                if source_was_processed(database_url, str(folder), signature):
                    queued_sources.add(key)
                    continue
                models = [item.strip() for item in os.getenv("OLLAMA_MODELS", "qwen2.5vl:7b").split(",") if item.strip()]
                job_id = uuid.uuid4().hex
                with jobs_lock:
                    jobs[job_id] = {
                        "status": "queued", "completed": 0, "total": 0, "rows": [], "error": None,
                        "database_job_id": None, "started_at": now, "finished_at": None, "folder": folder.name,
                    }
                    queued_sources.add(key)
                job_executor.submit(
                    run_job, job_id, root, [folder.name], models,
                    int(os.getenv("BOOK_WORKERS", "1")), app.config["OLLAMA_HOST"],
                    float(os.getenv("OLLAMA_TIMEOUT", "1800")), str(folder), signature,
                )
        except (OSError, DatabaseUnavailable):
            pass
        time.sleep(interval)


def start_folder_watcher() -> None:
    threading.Thread(target=watch_folders, name="folder-watcher", daemon=True).start()


def run_job(job_id: str, root: Path, selected: list[str] | None, models: list[str], workers: int, host: str, timeout: float, source_path: str | None = None, source_signature: str | None = None) -> None:
    """Run recognition outside the request and publish each completed row."""
    def progress(row: dict[str, str], completed: int, total: int) -> None:
        with jobs_lock:
            jobs[job_id].update(status="processing", completed=completed, total=total)
            jobs[job_id]["rows"].append(row)

    def start(total: int) -> None:
        with jobs_lock:
            jobs[job_id].update(status="processing", total=total)

    try:
        with jobs_lock:
            jobs[job_id]["status"] = "processing"
        rows = process_books(root, selected, models, workers, host, timeout, progress, start)
        database_url = os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor")
        wait_seconds = 2
        while True:
            try:
                with jobs_lock:
                    jobs[job_id].update(status="saving", error=None)
                database_job_id = save_books(database_url, rows, source_path or str(root), selected, models, source_signature)
                break
            except DatabaseUnavailable as error:
                with jobs_lock:
                    jobs[job_id].update(status="waiting_database", error=str(error))
                time.sleep(wait_seconds)
                wait_seconds = min(wait_seconds * 2, 60)
        with jobs_lock:
            jobs[job_id].update(
                status="completed", completed=len(rows), total=len(rows),
                database_job_id=database_job_id, finished_at=time.time(),
            )
    except Exception as error:  # The error must be visible to the polling browser.
        with jobs_lock:
            jobs[job_id].update(status="failed", error=str(error), finished_at=time.time())
            if source_path and source_signature:
                queued_sources.discard((source_path, source_signature))
                folder_candidates[source_path] = (source_signature, time.time())


def main() -> None:
    start_folder_watcher()
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
