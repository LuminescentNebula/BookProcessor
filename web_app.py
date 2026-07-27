#!/usr/bin/env python3
"""Small local web interface for BookProcessor."""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from book_processor import natural_key, process_books
from database import save_books

app = Flask(__name__)
app.config["OLLAMA_HOST"] = os.getenv("OLLAMA_HOST", "http://localhost:11434")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
job_executor = ThreadPoolExecutor(max_workers=int(os.getenv("WEB_JOB_WORKERS", "2")))


def folder_names(root: Path) -> list[str]:
    if not root.is_dir():
        raise ValueError("Корневая папка не найдена")
    return [path.name for path in sorted((p for p in root.iterdir() if p.is_dir()), key=natural_key)]


@app.get("/")
def index():
    return render_template("index.html", ollama_host=app.config["OLLAMA_HOST"])


@app.get("/api/folders")
def folders():
    try:
        return jsonify({"folders": folder_names(Path(request.args.get("root", "")))})
    except (OSError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


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
            jobs[job_id] = {"status": "queued", "completed": 0, "total": 0, "rows": [], "error": None, "database_job_id": None}
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
        return jsonify({**state, "rows": [dict(row) for row in state["rows"]]})


def run_job(job_id: str, root: Path, selected: list[str] | None, models: list[str], workers: int, host: str, timeout: float) -> None:
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
        database_job_id = save_books(
            os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor"),
            rows, str(root), selected, models,
        )
        with jobs_lock:
            jobs[job_id].update(status="completed", completed=len(rows), total=len(rows), database_job_id=database_job_id)
    except Exception as error:  # The error must be visible to the polling browser.
        with jobs_lock:
            jobs[job_id].update(status="failed", error=str(error))


def main() -> None:
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
