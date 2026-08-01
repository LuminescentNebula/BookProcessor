import math
import time
from pathlib import Path
from flask import Blueprint, current_app, jsonify, render_template, request
from .auth import admin_required

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
    return jsonify({
        "status": "delegated",
        "message": "Папки автоматически ставятся в очередь отдельным worker-сервисом",
    }), 202

@bp.get("/api/jobs/<job_id>")
@admin_required
def job_status(job_id):
    try:
        page = int(request.args.get("page", "1"))
        per_page = min(int(request.args.get("per_page", current_app.config["JOB_ROW_PAGE_SIZE"])), 100)
        if page < 1 or per_page < 1:
            raise ValueError
    except ValueError:
        return jsonify({"error": "Некорректные параметры страницы"}), 400
    job_state = state()
    with job_state.lock:
        item = job_state.jobs.get(job_id)
        if item is None:
            return jsonify({"error": "Обработка не найдена"}), 404
        end = item["finished_at"] or time.time()
        rows = item["rows"]
        total_pages = max(1, math.ceil(len(rows) / per_page))
        page = min(page, total_pages)
        result = {key: value for key, value in item.items() if key != "rows"}
        result.update(elapsed_seconds=max(0, int(end - item["started_at"])), page=page,
                      per_page=per_page, total_rows=len(rows), total_pages=total_pages)
        result["rows"] = [dict(row) for row in rows[(page - 1) * per_page:page * per_page]]
        return jsonify(result)

@bp.get("/api/jobs")
@admin_required
def all_jobs():
    job_state = state()
    with job_state.lock:
        result = []
        all_items = list(job_state.jobs.items())
        for job_id, item in reversed(all_items[-current_app.config["JOB_LIST_LIMIT"]:]):
            end = item["finished_at"] or time.time()
            summary = {key: value for key, value in item.items() if key != "rows"}
            result.append({"job_id": job_id, **summary, "row_count": len(item["rows"]), "elapsed_seconds": max(0, int(end - item["started_at"]))})
        return jsonify({"jobs": result, "total_jobs": len(all_items)})
