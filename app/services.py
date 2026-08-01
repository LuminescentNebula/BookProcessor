from types import SimpleNamespace
import database
import book_processor
from flask import current_app
from .config import DEFAULT_SETTINGS


def default_services():
    return SimpleNamespace(
        database=database,
        process_books=book_processor.process_books,
        normalize_metadata=book_processor.normalize_metadata,
        search_book_online=book_processor.search_book_online,
    )


def services():
    return current_app.extensions["bookprocessor_services"]


def database_url():
    return current_app.config["DATABASE_URL"]


def application_settings():
    return services().database.load_settings(database_url(), DEFAULT_SETTINGS)
