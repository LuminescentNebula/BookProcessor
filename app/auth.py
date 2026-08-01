from functools import wraps
from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash
from database import DatabaseUnavailable
from .services import database_url, services

bp = Blueprint("auth", __name__)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Требуется авторизация"}), 401
            return redirect(url_for("auth.login", next=request.path))
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

@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        try:
            user = services().database.find_user(database_url(), request.form.get("username", "").strip())
            if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
                session.clear()
                session.update(user_id=user["id"], username=user["username"], role=user["role"])
                target = request.args.get("next", "")
                return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("books.library"))
            error = "Неверное имя пользователя или пароль"
        except DatabaseUnavailable as exception:
            error = str(exception)
    return render_template("login.html", error=error)

@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
