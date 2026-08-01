import time
import uuid
from pathlib import Path
from flask import Blueprint, current_app, jsonify, render_template, request
from database import DatabaseUnavailable
from .auth import admin_required
from .services import application_settings, database_url, services

bp = Blueprint("jobs", __name__)

def state():
    return current_app.extensions["job_state"]

@bp.get("/api/folders")
@admin_required
def folders():
    root = Path(request.args.get("root", ""))
    if not root.is_dir():
        return jsonify({"error": "Корневая папка не найдена"}), 400
    return jsonify({"folders": sorted(path.name for path in root.iterdir() if path.is_dir())})

@bp.get("/")
@admin_required
def index():
    return render_template("index.html")

@bp.post("/process")
@admin_required
def process():
    try:
        root, settings = Path(request.form["root"]).expanduser(), application_settings()
        models = [item.strip() for item in settings["OLLAMA_MODELS"].split(",") if item.strip()]
        selected, workers, timeout = request.form.getlist("folders") or None, int(settings["BOOK_WORKERS"]), float(settings["OLLAMA_TIMEOUT"])
        if workers < 1 or timeout <= 0 or not models or not root.is_dir():
            raise ValueError("Проверьте папку, модели, количество потоков и тайм-аут")
        job_id, job_state = uuid.uuid4().hex, state()
        with job_state.lock:
            job_state.jobs[job_id] = {"status": "queued", "completed": 0, "total": 0, "rows": [], "error": None, "database_job_id": None, "started_at": time.time(), "finished_at": None}
        job_state.executor.submit(run_job, current_app._get_current_object(), job_id, root, selected, models, workers, settings["OLLAMA_HOST"], timeout)
        return jsonify({"job_id": job_id}), 202
    except (DatabaseUnavailable, KeyError, OSError, RuntimeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400

@bp.get("/api/jobs/<job_id>")
@admin_required
def job_status(job_id):
    job_state = state()
    with job_state.lock:
        item = job_state.jobs.get(job_id)
        if item is None:
            return jsonify({"error": "Обработка не найдена"}), 404
        end = item["finished_at"] or time.time()
        result = {**item, "elapsed_seconds": max(0, int(end - item["started_at"]))}
        result["rows"] = [dict(row) for row in item["rows"]]
        return jsonify(result)

@bp.get("/api/jobs")
@admin_required
def all_jobs():
    job_state = state()
    with job_state.lock:
        result = []
        for job_id, item in reversed(list(job_state.jobs.items())):
            end = item["finished_at"] or time.time()
            result.append({"job_id": job_id, **item, "elapsed_seconds": max(0, int(end - item["started_at"]))})
        return jsonify({"jobs": result})

def run_job(app, job_id, root, selected, models, workers, host, timeout, source_path=None, source_signature=None):
    with app.app_context():
        job_state, svc = state(), services()
        def progress(row, completed, total):
            with job_state.lock:
                job_state.jobs[job_id].update(status="processing", completed=completed, total=total)
                job_state.jobs[job_id]["rows"].append(row)
        def start(total):
            with job_state.lock:
                job_state.jobs[job_id].update(status="processing", total=total)
        try:
            with job_state.lock:
                job_state.jobs[job_id]["status"] = "processing"
            rows = svc.process_books(root, selected, models, workers, host, timeout, progress, start, None, svc.internet_search)
            wait_seconds = 2
            while True:
                try:
                    with job_state.lock:
                        job_state.jobs[job_id].update(status="saving", error=None)
                    db_job_id = svc.database.save_books(database_url(), rows, source_path or str(root), selected, models, source_signature)
                    break
                except DatabaseUnavailable as error:
                    with job_state.lock:
                        job_state.jobs[job_id].update(status="waiting_database", error=str(error))
                    time.sleep(wait_seconds); wait_seconds = min(wait_seconds * 2, 60)
            with job_state.lock:
                job_state.jobs[job_id].update(status="completed", completed=len(rows), total=len(rows), database_job_id=db_job_id, finished_at=time.time())
        except Exception as error:
            with job_state.lock:
                job_state.jobs[job_id].update(status="failed", error=str(error), finished_at=time.time())
                if source_path and source_signature:
                    job_state.queued_sources.discard((source_path, source_signature))
                    job_state.folder_candidates[source_path] = (source_signature, time.time())
