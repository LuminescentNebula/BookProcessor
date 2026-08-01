import os

DEFAULT_SETTINGS = {
    "OLLAMA_HOST": "http://host.docker.internal:11434",
    "OLLAMA_MODELS": "qwen2.5vl:7b",
    "NORMALIZATION_MODEL": "qwen2.5:14b",
    "BOOK_WORKERS": "1",
    "OLLAMA_TIMEOUT": "1800",
    "FOLDER_SCAN_INTERVAL": "5",
    "FOLDER_STABLE_SECONDS": "10",
}

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key")
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor")
    PHOTOS_ROOT = os.getenv("PHOTOS_ROOT", "/photos")
    WEB_JOB_WORKERS = int(os.getenv("WEB_JOB_WORKERS", "2"))
    START_FOLDER_WATCHER = False
    BOOTSTRAP_ADMIN = False
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me-now")
    OPEN_LIBRARY_TIMEOUT = float(os.getenv("OPEN_LIBRARY_TIMEOUT", "10"))
    OPEN_LIBRARY_RETRIES = int(os.getenv("OPEN_LIBRARY_RETRIES", "2"))
    OPEN_LIBRARY_MIN_INTERVAL = float(os.getenv("OPEN_LIBRARY_MIN_INTERVAL", "0.5"))
    GOOGLE_BOOKS_TIMEOUT = float(os.getenv("GOOGLE_BOOKS_TIMEOUT", "10"))
    GOOGLE_BOOKS_RETRIES = int(os.getenv("GOOGLE_BOOKS_RETRIES", "2"))
    GOOGLE_BOOKS_MIN_INTERVAL = float(os.getenv("GOOGLE_BOOKS_MIN_INTERVAL", "0.5"))
    GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY", "")
