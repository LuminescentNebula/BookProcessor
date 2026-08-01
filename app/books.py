import math
import time
import urllib.parse
import uuid
from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file
from database import DatabaseUnavailable
from book_processor import FIELDS
from .auth import admin_required, login_required
from .services import application_settings, database_url, services

bp = Blueprint("books", __name__)

def _state():
    return current_app.extensions["job_state"]

def book_page(template):
    filters = {name: request.args.get(name, "") for name in ("author", "title", "publisher", "genre", "author_select", "publisher_select", "genre_select")}
    sort, direction = request.args.get("sort", "id"), request.args.get("direction", "desc")
    try:
        requested_page = int(request.args.get("page", "1"))
    except ValueError:
        requested_page = 1
    page_size = min(max(int(current_app.config["BOOK_PAGE_SIZE"]), 1), 100)
    sort_urls = {}
    for column in ("box", "author", "title", "year", "publisher", "isbn", "genre"):
        arguments = {key: value for key, value in filters.items() if value}
        arguments.update(sort=column, direction="desc" if sort == column and direction == "asc" else "asc")
        sort_urls[column] = request.path + "?" + urllib.parse.urlencode(arguments)
    try:
        db = services().database
        total = db.count_books(database_url(), filters)
        total_pages = max(1, math.ceil(total / page_size))
        page = min(max(requested_page, 1), total_pages)
        books = db.list_books(database_url(), filters, sort, direction, page_size, (page - 1) * page_size)
        query = {key: value for key, value in {**filters, "sort": sort, "direction": direction}.items() if value}
        def page_url(target):
            return request.path + "?" + urllib.parse.urlencode({**query, "page": target})
        pagination = {"page": page, "total": total, "total_pages": total_pages,
                      "previous_url": page_url(page - 1) if page > 1 else None,
                      "next_url": page_url(page + 1) if page < total_pages else None}
        return render_template(template, books=books, known=db.known_book_values(database_url()), filters=filters, sort=sort, direction=direction, sort_urls=sort_urls, pagination=pagination)
    except DatabaseUnavailable as error:
        pagination = {"page": 1, "total": 0, "total_pages": 1, "previous_url": None, "next_url": None}
        return render_template(template, books=[], known={"author": [], "publisher": [], "genre": []}, filters=filters, error=str(error), sort=sort, direction=direction, sort_urls=sort_urls, pagination=pagination), 503

@bp.get("/library")
@login_required
def library():
    return book_page("library.html")

@bp.get("/books")
@login_required
def books_table():
    return book_page("books.html")

@bp.get("/api/books/<int:book_id>/image/<image_kind>")
@login_required
def book_image(book_id, image_kind):
    if image_kind not in {"cover", "info"}:
        abort(404)
    path = services().database.book_image_path(database_url(), book_id, image_kind)
    if path is None or not path.is_file():
        abort(404)
    return send_file(path)

@bp.patch("/api/books/<int:book_id>")
@admin_required
def edit_book(book_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидается JSON-объект с изменениями"}), 400
    try:
        if not services().database.update_book(database_url(), book_id, payload):
            return jsonify({"error": "Книга не найдена"}), 404
        return jsonify({"ok": True})
    except (DatabaseUnavailable, ValueError) as error:
        return jsonify({"error": str(error)}), 503 if isinstance(error, DatabaseUnavailable) else 400

@bp.post("/api/books/normalize")
@admin_required
def normalize_selected_books():
    raw_ids = (request.get_json(silent=True) or {}).get("book_ids", [])
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 500:
        return jsonify({"error": "Выберите от 1 до 500 книг"}), 400
    try:
        book_ids = list(dict.fromkeys(int(value) for value in raw_ids))
        settings = application_settings()
        if not settings["NORMALIZATION_MODEL"].strip():
            raise ValueError("Сначала укажите модель нормализации в настройках")
        selected = services().database.get_books(database_url(), book_ids)
        if not selected:
            raise ValueError("Выбранные книги не найдены")
        job_id, state = uuid.uuid4().hex, _state()
        with state.lock:
            state.jobs[job_id] = {"status": "normalizing", "completed": 0, "total": len(selected), "rows": [], "error": None, "database_job_id": None, "started_at": time.time(), "finished_at": None, "folder": "Интернет-нормализация"}
        state.executor.submit(run_normalization_job, current_app._get_current_object(), job_id, selected, settings)
        return jsonify({"job_id": job_id}), 202
    except (DatabaseUnavailable, TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400

def run_normalization_job(app, job_id, books, settings):
    russian = {"Автор": "author", "Название": "title", "Год": "publication_year", "Издательство": "publisher", "Тираж": "print_run", "Язык": "language", "ISBN": "isbn", "Жанр": "genre"}
    with app.app_context():
        state, svc = _state(), services()
        try:
            for completed, book in enumerate(books, 1):
                metadata = {label: str(book.get(column) or "") for label, column in russian.items()}
                evidence = [item.as_dict() for item in svc.internet_search.search(metadata)]
                normalized = svc.normalize_metadata(metadata, evidence, settings["NORMALIZATION_MODEL"], settings["OLLAMA_HOST"], float(settings["OLLAMA_TIMEOUT"]))
                merged = {label: normalized[label] or metadata[label] for label in FIELDS}
                if merged["Тираж"]:
                    merged["Тираж"] = "".join(char for char in merged["Тираж"] if char.isdigit())
                svc.database.update_book(database_url(), book["id"], {column: merged[label] for label, column in russian.items()})
                with state.lock:
                    state.jobs[job_id]["rows"].append({"Коробка": book["box"], **merged})
                    state.jobs[job_id].update(completed=completed, status="normalizing")
            with state.lock:
                state.jobs[job_id].update(status="completed", finished_at=time.time())
        except Exception as error:
            with state.lock:
                state.jobs[job_id].update(status="failed", error=str(error), finished_at=time.time())
