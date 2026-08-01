#!/usr/bin/env python3
"""Small local web interface for BookProcessor."""

from __future__ import annotations

import os
import threading
import time
import uuid
import hashlib
import urllib.parse
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from book_processor import FIELDS, IMAGE_EXTENSIONS, natural_key, normalize_metadata, process_books, search_book_online
from database import DatabaseUnavailable, book_image_path, check_database, create_user, delete_user, ensure_admin, find_user, get_books, known_book_values, list_books, list_users, load_settings, save_books, save_settings, source_was_processed, update_book, update_user

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")
DEFAULT_SETTINGS = {
    "OLLAMA_HOST": "http://host.docker.internal:11434",
    "OLLAMA_MODELS": "qwen2.5vl:7b",
    "NORMALIZATION_MODEL": "qwen2.5:14b",
    "BOOK_WORKERS": "1",
    "OLLAMA_TIMEOUT": "1800",
    "FOLDER_SCAN_INTERVAL": "5",
    "FOLDER_STABLE_SECONDS": "10",
}
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
job_executor = ThreadPoolExecutor(max_workers=int(os.getenv("WEB_JOB_WORKERS", "2")))
queued_sources: set[tuple[str, str]] = set()
folder_candidates: dict[str, tuple[str, float]] = {}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Требуется авторизация"}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            if request.path.startswith("/api/"):
                return jsonify({"error": "Недостаточно прав"}), 403
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        try:
            user = find_user(database_url(), request.form.get("username", "").strip())
            if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
                session.clear()
                session.update(user_id=user["id"], username=user["username"], role=user["role"])
                target = request.args.get("next", "")
                return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("library"))
            error = "Неверное имя пользователя или пароль"
        except DatabaseUnavailable as exception:
            error = str(exception)
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def folder_names(root: Path) -> list[str]:
    if not root.is_dir():
        raise ValueError("Корневая папка не найдена")
    return [path.name for path in sorted((p for p in root.iterdir() if p.is_dir()), key=natural_key)]


@app.get("/")
@admin_required
def index():
    return render_template("index.html")


def database_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor")


def application_settings() -> dict[str, str]:
    return load_settings(database_url(), DEFAULT_SETTINGS)


@app.route("/settings", methods=["GET", "POST"])
@admin_required
def settings_page():
    error = None
    try:
        if request.method == "POST":
            values = {key: request.form.get(key, "").strip() for key in DEFAULT_SETTINGS}
            if not values["OLLAMA_HOST"].startswith(("http://", "https://")):
                raise ValueError("OLLAMA_HOST должен начинаться с http:// или https://")
            if not values["OLLAMA_MODELS"]:
                raise ValueError("Укажите хотя бы одну модель Ollama")
            if values["NORMALIZATION_MODEL"] in {item.strip() for item in values["OLLAMA_MODELS"].split(",")}:
                raise ValueError("Для нормализации выберите другую модель Ollama")
            for key in ("BOOK_WORKERS", "OLLAMA_TIMEOUT", "FOLDER_SCAN_INTERVAL", "FOLDER_STABLE_SECONDS"):
                if float(values[key]) <= 0:
                    raise ValueError(f"{key} должен быть больше нуля")
            if int(values["BOOK_WORKERS"]) != float(values["BOOK_WORKERS"]):
                raise ValueError("BOOK_WORKERS должен быть целым числом")
            save_settings(database_url(), values)
            return redirect(url_for("settings_page", saved="1"))
        values = application_settings()
    except (DatabaseUnavailable, ValueError) as exception:
        error = str(exception)
        values = request.form or DEFAULT_SETTINGS
    return render_template("settings.html", settings=values, error=error, saved=request.args.get("saved") == "1")


@app.route("/users", methods=["GET", "POST"])
@admin_required
def users_page():
    error = request.args.get("error")
    try:
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "viewer")
            if len(username) < 3 or len(password) < 8:
                raise ValueError("Имя должно содержать минимум 3 символа, пароль — минимум 8")
            create_user(database_url(), username, generate_password_hash(password), role)
            return redirect(url_for("users_page"))
        users = list_users(database_url())
    except (DatabaseUnavailable, ValueError) as exception:
        error = str(exception)
        users = []
    return render_template("users.html", users=users, error=error)


@app.post("/users/<int:user_id>")
@admin_required
def change_user(user_id: int):
    if user_id == session["user_id"]:
        return redirect(url_for("users_page", error="Нельзя изменить собственную роль или удалить себя"))
    action = request.form.get("action", "update")
    try:
        if action == "delete":
            delete_user(database_url(), user_id)
        else:
            password = request.form.get("password", "")
            if password and len(password) < 8:
                raise ValueError("Новый пароль должен содержать минимум 8 символов")
            update_user(
                database_url(), user_id, request.form.get("role", "viewer"),
                generate_password_hash(password) if password else None,
            )
        return redirect(url_for("users_page"))
    except (DatabaseUnavailable, ValueError) as error:
        return redirect(url_for("users_page", error=str(error)))


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
@login_required
def library():
    return book_page("library.html")


@app.get("/books")
@login_required
def books_table():
    return book_page("books.html")


@app.get("/api/books/<int:book_id>/image/<image_kind>")
@login_required
def book_image(book_id: int, image_kind: str):
    if image_kind not in {"cover", "info"}:
        abort(404)
    path = book_image_path(database_url(), book_id, image_kind)
    if path is None or not path.is_file():
        abort(404)
    return send_file(path)


@app.patch("/api/books/<int:book_id>")
@admin_required
def edit_book(book_id: int):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидается JSON-объект с изменениями"}), 400
    try:
        if not update_book(database_url(), book_id, payload):
            return jsonify({"error": "Книга не найдена"}), 404
        return jsonify({"ok": True})
    except (DatabaseUnavailable, ValueError) as error:
        return jsonify({"error": str(error)}), 503 if isinstance(error, DatabaseUnavailable) else 400


@app.post("/api/books/normalize")
@admin_required
def normalize_selected_books():
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("book_ids", [])
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 500:
        return jsonify({"error": "Выберите от 1 до 500 книг"}), 400
    try:
        book_ids = list(dict.fromkeys(int(value) for value in raw_ids))
        settings = application_settings()
        model = settings["NORMALIZATION_MODEL"].strip()
        if not model:
            raise ValueError("Сначала укажите модель нормализации в настройках")
        selected = get_books(database_url(), book_ids)
        if not selected:
            raise ValueError("Выбранные книги не найдены")
        job_id = uuid.uuid4().hex
        with jobs_lock:
            jobs[job_id] = {
                "status": "normalizing", "completed": 0, "total": len(selected), "rows": [],
                "error": None, "database_job_id": None, "started_at": time.time(),
                "finished_at": None, "folder": "Интернет-нормализация",
            }
        job_executor.submit(run_normalization_job, job_id, selected, settings)
        return jsonify({"job_id": job_id}), 202
    except (DatabaseUnavailable, TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


def run_normalization_job(job_id: str, books: list[dict], settings: dict[str, str]) -> None:
    """Normalize explicitly selected database rows without rerunning photo OCR."""
    russian = {
        "Автор": "author", "Название": "title", "Год": "publication_year",
        "Издательство": "publisher", "Тираж": "print_run", "Язык": "language",
        "ISBN": "isbn", "Жанр": "genre",
    }
    try:
        for completed, book in enumerate(books, 1):
            metadata = {label: str(book.get(column) or "") for label, column in russian.items()}
            normalized = normalize_metadata(
                metadata, search_book_online(metadata), settings["NORMALIZATION_MODEL"],
                settings["OLLAMA_HOST"], float(settings["OLLAMA_TIMEOUT"]),
            )
            merged = {label: normalized[label] or metadata[label] for label in FIELDS}
            if merged["Тираж"]:
                merged["Тираж"] = "".join(character for character in merged["Тираж"] if character.isdigit())
            changes = {column: merged[label] for label, column in russian.items()}
            update_book(database_url(), book["id"], changes)
            with jobs_lock:
                jobs[job_id]["rows"].append({"Коробка": book["box"], **merged})
                jobs[job_id].update(completed=completed, status="normalizing")
        with jobs_lock:
            jobs[job_id].update(status="completed", finished_at=time.time())
    except Exception as error:
        with jobs_lock:
            jobs[job_id].update(status="failed", error=str(error), finished_at=time.time())


@app.get("/api/folders")
@admin_required
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
@admin_required
def health():
    database_ok, database_message = check_database(os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor"))
    try:
        settings = application_settings()
        ollama_ok, ollama_message = check_ollama(settings["OLLAMA_HOST"])
    except DatabaseUnavailable as error:
        ollama_ok, ollama_message = False, f"Настройки недоступны: {error}"
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
@admin_required
def process():
    try:
        root = Path(request.form["root"]).expanduser()
        settings = application_settings()
        models = [item.strip() for item in settings["OLLAMA_MODELS"].split(",") if item.strip()]
        selected = request.form.getlist("folders") or None
        workers = int(settings["BOOK_WORKERS"])
        timeout = float(settings["OLLAMA_TIMEOUT"])
        if workers < 1 or timeout <= 0 or not models or not root.is_dir():
            raise ValueError("Проверьте папку, модели, количество потоков и тайм-аут")
        job_id = uuid.uuid4().hex
        with jobs_lock:
            jobs[job_id] = {
                "status": "queued", "completed": 0, "total": 0, "rows": [],
                "error": None, "database_job_id": None, "started_at": time.time(),
                "finished_at": None,
            }
        job_executor.submit(
            run_job, job_id, root, selected, models, workers, settings["OLLAMA_HOST"], timeout,
            None, None, None,
        )
        return jsonify({"job_id": job_id}), 202
    except (DatabaseUnavailable, KeyError, OSError, RuntimeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.get("/api/jobs/<job_id>")
@admin_required
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
@admin_required
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
    database_url = os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor")
    while True:
        try:
            settings = application_settings()
            interval = float(settings["FOLDER_SCAN_INTERVAL"])
            stable_seconds = float(settings["FOLDER_STABLE_SECONDS"])
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
                models = [item.strip() for item in settings["OLLAMA_MODELS"].split(",") if item.strip()]
                job_id = uuid.uuid4().hex
                with jobs_lock:
                    jobs[job_id] = {
                        "status": "queued", "completed": 0, "total": 0, "rows": [], "error": None,
                        "database_job_id": None, "started_at": now, "finished_at": None, "folder": folder.name,
                    }
                    queued_sources.add(key)
                job_executor.submit(
                    run_job, job_id, root, [folder.name], models,
                    int(settings["BOOK_WORKERS"]), settings["OLLAMA_HOST"],
                    float(settings["OLLAMA_TIMEOUT"]), str(folder), signature,
                    None,
                )
        except (OSError, DatabaseUnavailable):
            interval = 5
        time.sleep(interval)


def start_folder_watcher() -> None:
    threading.Thread(target=watch_folders, name="folder-watcher", daemon=True).start()


def run_job(job_id: str, root: Path, selected: list[str] | None, models: list[str], workers: int, host: str, timeout: float, source_path: str | None = None, source_signature: str | None = None, normalization_model: str | None = None) -> None:
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
        rows = process_books(root, selected, models, workers, host, timeout, progress, start, normalization_model)
        database_url = os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor")
        wait_seconds = 2
        while True:
            try:
                with jobs_lock:
                    jobs[job_id].update(status="saving", error=None)
                stored_models = [*models, *([normalization_model] if normalization_model else [])]
                database_job_id = save_books(database_url, rows, source_path or str(root), selected, stored_models, source_signature)
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
    ensure_admin(
        database_url(), os.getenv("ADMIN_USERNAME", "admin"),
        generate_password_hash(os.getenv("ADMIN_PASSWORD", "change-me-now")),
    )
    start_folder_watcher()
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
