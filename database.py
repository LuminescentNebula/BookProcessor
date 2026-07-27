"""PostgreSQL persistence for processed books."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS processing_jobs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    photo_root TEXT NOT NULL,
    folders JSONB NOT NULL,
    models JSONB NOT NULL
);
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
CREATE INDEX IF NOT EXISTS books_job_id_idx ON books(job_id);
CREATE INDEX IF NOT EXISTS books_isbn_idx ON books(isbn);
"""

INSERT_BOOK = """
INSERT INTO books (
    job_id, box, author, title, publication_year, publisher, print_run,
    language, isbn, cover_filename, info_filename
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def save_books(
    database_url: str,
    rows: Iterable[dict[str, str]],
    photo_root: str,
    folders: list[str] | None,
    models: list[str],
) -> int:
    """Save one processing run atomically and return its job id."""
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError("Для PostgreSQL установите зависимости: pip install -r requirements.txt") from error

    try:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA)
                cursor.execute(
                    "INSERT INTO processing_jobs (photo_root, folders, models) VALUES (%s, %s::jsonb, %s::jsonb) RETURNING id",
                    (photo_root, json.dumps(folders or [], ensure_ascii=False), json.dumps(models, ensure_ascii=False)),
                )
                job_id = cursor.fetchone()[0]
                values: list[tuple[Any, ...]] = []
                for row in rows:
                    values.append((
                        job_id, row["Коробка"], row["Автор"] or None, row["Название"] or None,
                        row["Год"] or None, row["Издательство"] or None, row["Тираж"] or None,
                        row["Язык"] or None, row["ISBN"] or None,
                        row["Название файла фото обложки"], row["Название файла фото информации"],
                    ))
                cursor.executemany(INSERT_BOOK, values)
    except psycopg.Error as error:
        raise RuntimeError(f"Ошибка PostgreSQL: {error}") from error
    return job_id
