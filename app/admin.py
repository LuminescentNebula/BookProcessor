from flask import Blueprint, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash
from database import DatabaseUnavailable
from .auth import admin_required
from .config import DEFAULT_SETTINGS
from .services import application_settings, database_url, services

bp = Blueprint("admin", __name__)

@bp.route("/settings", methods=["GET", "POST"])
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
            services().database.save_settings(database_url(), values)
            return redirect(url_for("admin.settings_page", saved="1"))
        values = application_settings()
    except (DatabaseUnavailable, ValueError) as exception:
        error, values = str(exception), request.form or DEFAULT_SETTINGS
    return render_template("settings.html", settings=values, error=error, saved=request.args.get("saved") == "1")

@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users_page():
    error = request.args.get("error")
    try:
        if request.method == "POST":
            username, password, role = request.form.get("username", "").strip(), request.form.get("password", ""), request.form.get("role", "viewer")
            if len(username) < 3 or len(password) < 8:
                raise ValueError("Имя должно содержать минимум 3 символа, пароль — минимум 8")
            services().database.create_user(database_url(), username, generate_password_hash(password), role)
            return redirect(url_for("admin.users_page"))
        users = services().database.list_users(database_url())
    except (DatabaseUnavailable, ValueError) as exception:
        error, users = str(exception), []
    return render_template("users.html", users=users, error=error)

@bp.post("/users/<int:user_id>")
@admin_required
def change_user(user_id):
    if user_id == session["user_id"]:
        return redirect(url_for("admin.users_page", error="Нельзя изменить собственную роль или удалить себя"))
    try:
        if request.form.get("action", "update") == "delete":
            services().database.delete_user(database_url(), user_id)
        else:
            password = request.form.get("password", "")
            if password and len(password) < 8:
                raise ValueError("Новый пароль должен содержать минимум 8 символов")
            services().database.update_user(database_url(), user_id, request.form.get("role", "viewer"), generate_password_hash(password) if password else None)
        return redirect(url_for("admin.users_page"))
    except (DatabaseUnavailable, ValueError) as error:
        return redirect(url_for("admin.users_page", error=str(error)))
