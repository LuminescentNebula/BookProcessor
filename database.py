"""PostgreSQL persistence for processed books."""

from __future__ import annotations

import json
import importlib
from pathlib import Path
from collections.abc import Iterable
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS processing_jobs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    photo_root TEXT NOT NULL,
    folders JSONB NOT NULL,
    models JSONB NOT NULL,
    source_signature TEXT
);
ALTER TABLE processing_jobs ADD COLUMN IF NOT EXISTS source_signature TEXT;
CREATE TABLE IF NOT EXISTS books (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES processing_jobs(id) ON DELETE CASCADE,
    box TEXT NOT NULL,
    author TEXT,
    title TEXT,
    publication_year TEXT,
    publisher TEXT,
    print_run TEXT,
    language TEXT,
    isbn TEXT,
    cover_filename TEXT NOT NULL,
    info_filename TEXT NOT NULL
);
ALTER TABLE books ADD COLUMN IF NOT EXISTS genre TEXT;
CREATE INDEX IF NOT EXISTS books_job_id_idx ON books(job_id);
CREATE INDEX IF NOT EXISTS books_isbn_idx ON books(isbn);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS app_users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

INSERT_BOOK = """
INSERT INTO books (
    job_id, box, author, title, publication_year, publisher, print_run,
    language, isbn, genre, cover_filename, info_filename
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class DatabaseUnavailable(RuntimeError):
    """PostgreSQL cannot currently be reached."""


def check_database(database_url: str) -> tuple[bool, str]:
    """Check PostgreSQL without changing data."""
    try:
        psycopg = importlib.import_module("psycopg")
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            connection.execute("SELECT 1")
        return True, "PostgreSQL доступна"
    except Exception as error:
        return False, f"PostgreSQL недоступна: {error}"


def load_settings(database_url: str, defaults: dict[str, str]) -> dict[str, str]:
    """Load application settings, filling keys that have not been saved yet."""
    psycopg = importlib.import_module("psycopg")
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            connection.execute(SCHEMA)
            rows = connection.execute("SELECT key, value FROM app_settings").fetchall()
        return {**defaults, **dict(rows)}
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def save_settings(database_url: str, settings: dict[str, str]) -> None:
    """Upsert validated application settings."""
    psycopg = importlib.import_module("psycopg")
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            connection.execute(SCHEMA)
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO app_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
                    list(settings.items()),
                )
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def ensure_admin(database_url: str, username: str, password_hash: str) -> None:
    """Create the initial administrator only when no users exist."""
    psycopg = importlib.import_module("psycopg")
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            connection.execute(SCHEMA)
            connection.execute(
                "INSERT INTO app_users (username, password_hash, role) SELECT %s, %s, 'admin' WHERE NOT EXISTS (SELECT 1 FROM app_users)",
                (username, password_hash),
            )
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def find_user(database_url: str, username: str) -> dict[str, Any] | None:
    psycopg = importlib.import_module("psycopg")
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            row = connection.execute(
                "SELECT id, username, password_hash, role FROM app_users WHERE username = %s", (username,),
            ).fetchone()
        return dict(zip(("id", "username", "password_hash", "role"), row)) if row else None
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def list_users(database_url: str) -> list[dict[str, Any]]:
    psycopg = importlib.import_module("psycopg")
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            rows = connection.execute("SELECT id, username, role, created_at FROM app_users ORDER BY username").fetchall()
        return [dict(zip(("id", "username", "role", "created_at"), row)) for row in rows]
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def create_user(database_url: str, username: str, password_hash: str, role: str) -> None:
    if role not in {"admin", "viewer"}:
        raise ValueError("Недопустимая роль")
    psycopg = importlib.import_module("psycopg")
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            connection.execute(
                "INSERT INTO app_users (username, password_hash, role) VALUES (%s, %s, %s)",
                (username, password_hash, role),
            )
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error
    except psycopg.Error as error:
        raise ValueError(f"Не удалось создать пользователя: {error}") from error


def update_user(database_url: str, user_id: int, role: str, password_hash: str | None) -> bool:
    if role not in {"admin", "viewer"}:
        raise ValueError("Недопустимая роль")
    psycopg = importlib.import_module("psycopg")
    query = "UPDATE app_users SET role = %s" + (", password_hash = %s" if password_hash else "") + " WHERE id = %s"
    values = [role, *([password_hash] if password_hash else []), user_id]
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            return connection.execute(query, values).rowcount > 0
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def delete_user(database_url: str, user_id: int) -> bool:
    psycopg = importlib.import_module("psycopg")
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            return connection.execute("DELETE FROM app_users WHERE id = %s", (user_id,)).rowcount > 0
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def source_was_processed(database_url: str, photo_root: str, source_signature: str) -> bool:
    """Return whether this exact version of a mounted folder was saved."""
    psycopg = importlib.import_module("psycopg")
    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            connection.execute(SCHEMA)
            row = connection.execute(
                "SELECT 1 FROM processing_jobs WHERE photo_root = %s AND source_signature = %s LIMIT 1",
                (photo_root, source_signature),
            ).fetchone()
        return row is not None
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def save_books(
    database_url: str,
    rows: Iterable[dict[str, str]],
    photo_root: str,
    folders: list[str] | None,
    models: list[str],
    source_signature: str | None = None,
) -> int:
    """Save one processing run atomically and return its job id."""
    try:
        psycopg = importlib.import_module("psycopg")
    except ModuleNotFoundError as error:
        raise RuntimeError("Для PostgreSQL установите зависимости: pip install -r requirements.txt") from error

    try:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA)
                cursor.execute(
                    "INSERT INTO processing_jobs (photo_root, folders, models, source_signature) VALUES (%s, %s::jsonb, %s::jsonb, %s) RETURNING id",
                    (photo_root, json.dumps(folders or [], ensure_ascii=False), json.dumps(models, ensure_ascii=False), source_signature),
                )
                job_id = cursor.fetchone()[0]
                values: list[tuple[Any, ...]] = []
                for row in rows:
                    values.append((
                        job_id, row["Коробка"], row["Автор"] or None, row["Название"] or None,
                        row["Год"] or None, row["Издательство"] or None, row["Тираж"] or None,
                        row["Язык"] or None, row["ISBN"] or None, row.get("Жанр") or None,
                        row["Название файла фото обложки"], row["Название файла фото информации"],
                    ))
                cursor.executemany(INSERT_BOOK, values)
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error
    except psycopg.Error as error:
        raise RuntimeError(f"Ошибка PostgreSQL: {error}") from error
    return job_id


BOOK_COLUMNS = {
    "author": "b.author", "title": "b.title", "publisher": "b.publisher",
    "genre": "b.genre", "year": "b.publication_year", "isbn": "b.isbn",
    "box": "b.box", "id": "b.id",
}

EDITABLE_BOOK_COLUMNS = {
    "box": "box", "author": "author", "title": "title",
    "publication_year": "publication_year", "publisher": "publisher",
    "print_run": "print_run", "language": "language", "isbn": "isbn",
    "genre": "genre",
}


def update_book(database_url: str, book_id: int, changes: dict[str, Any]) -> bool:
    """Update allowlisted book fields and return whether the book exists."""
    psycopg = importlib.import_module("psycopg")
    clean = {name: str(value).strip() or None for name, value in changes.items() if name in EDITABLE_BOOK_COLUMNS}
    if not clean:
        raise ValueError("Нет допустимых полей для изменения")
    assignments = ", ".join(f"{EDITABLE_BOOK_COLUMNS[name]} = %s" for name in clean)
    values = [*clean.values(), book_id]
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            result = connection.execute(f"UPDATE books SET {assignments} WHERE id = %s", values)
            return result.rowcount > 0
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def get_books(database_url: str, book_ids: list[int]) -> list[dict[str, Any]]:
    """Load a selected set of books in caller-provided order."""
    if not book_ids:
        return []
    psycopg = importlib.import_module("psycopg")
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, box, author, title, publication_year, publisher, print_run, language, isbn, genre FROM books WHERE id = ANY(%s)",
                    (book_ids,),
                )
                names = [column.name for column in cursor.description]
                found = {row[0]: dict(zip(names, row)) for row in cursor.fetchall()}
        return [found[book_id] for book_id in book_ids if book_id in found]
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


MAX_BOOK_PAGE_SIZE = 100


def _book_filters(filters: dict[str, str]) -> tuple[list[str], list[str]]:
    clauses: list[str] = []
    values: list[str] = []
    for name in ("author", "title", "publisher", "genre"):
        text = filters.get(name, "").strip()
        selected = filters.get(f"{name}_select", "").strip()
        if text:
            clauses.append(f"{BOOK_COLUMNS[name]} ILIKE %s")
            values.append(f"%{text}%")
        if selected:
            clauses.append(f"{BOOK_COLUMNS[name]} = %s")
            values.append(selected)
    return clauses, values


def list_books(database_url: str, filters: dict[str, str], sort: str = "id", direction: str = "desc", limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Load processed books using parameterized filters and an allowlisted order."""
    psycopg = importlib.import_module("psycopg")
    clauses, values = _book_filters(filters)
    limit, offset = min(max(int(limit), 1), MAX_BOOK_PAGE_SIZE), max(int(offset), 0)
    order = BOOK_COLUMNS.get(sort, BOOK_COLUMNS["id"])
    order_direction = "ASC" if direction.casefold() == "asc" else "DESC"
    query = f"""
        SELECT b.id, b.box, b.author, b.title, b.publication_year, b.publisher,
               b.print_run, b.language, b.isbn, b.genre, b.cover_filename,
               b.info_filename
        FROM books b
        {"WHERE " + " AND ".join(clauses) if clauses else ""}
        ORDER BY {order} {order_direction} NULLS LAST, b.id DESC
        LIMIT %s OFFSET %s
    """
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA)
                cursor.execute(query, [*values, limit, offset])
                names = [column.name for column in cursor.description]
                return [dict(zip(names, row)) for row in cursor.fetchall()]
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def count_books(database_url: str, filters: dict[str, str]) -> int:
    """Count books using the same filters as ``list_books``."""
    psycopg = importlib.import_module("psycopg")
    clauses, values = _book_filters(filters)
    query = f'SELECT COUNT(*) FROM books b {"WHERE " + " AND ".join(clauses) if clauses else ""}'
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            return int(connection.execute(query, values).fetchone()[0])
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def known_book_values(database_url: str) -> dict[str, list[str]]:
    """Return distinct values for filter dropdowns."""
    psycopg = importlib.import_module("psycopg")
    result: dict[str, list[str]] = {}
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            connection.execute(SCHEMA)
            for name in ("author", "publisher", "genre"):
                rows = connection.execute(
                    f"SELECT DISTINCT {BOOK_COLUMNS[name]} FROM books b WHERE {BOOK_COLUMNS[name]} IS NOT NULL AND {BOOK_COLUMNS[name]} <> '' ORDER BY {BOOK_COLUMNS[name]}"
                ).fetchall()
                result[name] = [row[0] for row in rows]
        return result
    except psycopg.OperationalError as error:
        raise DatabaseUnavailable(f"PostgreSQL временно недоступна: {error}") from error


def book_image_path(database_url: str, book_id: int, image_kind: str) -> Path | None:
    """Resolve a stored cover or publishing-info image path."""
    filename_column = "cover_filename" if image_kind == "cover" else "info_filename"
    psycopg = importlib.import_module("psycopg")
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        row = connection.execute(
            f"SELECT j.photo_root, b.box, b.{filename_column} FROM books b JOIN processing_jobs j ON j.id = b.job_id WHERE b.id = %s",
            (book_id,),
        ).fetchone()
    if not row:
        return None
    direct = Path(row[0]) / row[2]
    return direct if direct.is_file() else Path(row[0]) / row[1] / row[2]
