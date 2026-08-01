import json
import urllib.error
import urllib.request
from pathlib import Path
from flask import Blueprint, current_app, jsonify
from database import DatabaseUnavailable
from .auth import admin_required
from .services import application_settings, database_url, services

bp = Blueprint("health", __name__)

def check_ollama(host):
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=3) as response:
            json.load(response)
        return True, "Ollama доступна"
    except (OSError, ValueError, urllib.error.URLError) as error:
        return False, f"Ollama недоступна: {error}"

@bp.get("/api/health")
@admin_required
def health():
    database_ok, database_message = services().database.check_database(database_url())
    try:
        ollama_ok, ollama_message = check_ollama(application_settings()["OLLAMA_HOST"])
    except DatabaseUnavailable as error:
        ollama_ok, ollama_message = False, f"Настройки недоступны: {error}"
    root = Path(current_app.config["PHOTOS_ROOT"])
    photos_ok = root.is_dir()
    photos_message = f"Каталог фотографий: {root}" if photos_ok else f"Mounted-каталог не найден: {root}"
    providers = services().internet_search.health()
    providers_ok = not providers or any(item.get("status") != "unavailable" for item in providers)
    return jsonify(
        status="ready" if database_ok and ollama_ok and photos_ok and providers_ok else "degraded",
        database={"ok": database_ok, "message": database_message},
        ollama={"ok": ollama_ok, "message": ollama_message},
        photos={"ok": photos_ok, "message": photos_message},
        providers=providers,
    )
