#!/usr/bin/env python3
"""Extract book metadata from pairs of photographs using local Ollama models."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


FIELDS = ("Автор", "Название", "Год", "Издательство", "Тираж", "Язык", "ISBN", "Жанр")
COLUMNS = ("Коробка", *FIELDS, "Название файла фото обложки", "Название файла фото информации")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


@dataclass(frozen=True)
class PhotoPair:
    box: str
    cover: Path
    info: Path


def natural_key(path: Path) -> list[str | int]:
    """Sort file names in the order humans expect (2.jpg before 10.jpg)."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", path.name)]


def discover_pairs(root: Path, folders: list[str] | None) -> list[PhotoPair]:
    if folders:
        directories = [root / name for name in folders]
        missing = [str(path) for path in directories if not path.is_dir()]
        if missing:
            raise ValueError("Папки не найдены: " + ", ".join(missing))
    else:
        directories = sorted((p for p in root.iterdir() if p.is_dir()), key=natural_key)

    pairs: list[PhotoPair] = []
    for directory in directories:
        photos = sorted(
            (p for p in directory.iterdir() if p.is_file() and p.suffix.casefold() in IMAGE_EXTENSIONS),
            key=natural_key,
        )
        if len(photos) % 2:
            print(f"Предупреждение: {directory}: последнее фото без пары пропущено", file=sys.stderr)
        pairs.extend(PhotoPair(directory.name, photos[i], photos[i + 1]) for i in range(0, len(photos) - 1, 2))
    return pairs


def _data_url(path: Path) -> str:
    """Downscale camera photos before sending them to avoid huge Ollama requests."""
    from PIL import Image, ImageOps

    max_size = int(os.getenv("MAX_IMAGE_SIZE", "2048"))
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _extract_json(text: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("модель не вернула JSON")
        value = json.loads(match.group())
    if not isinstance(value, dict):
        raise ValueError("ответ модели не является объектом JSON")
    return value


def query_ollama(pair: PhotoPair, model: str, host: str, timeout: float) -> dict[str, str]:
    schema = {field: "" for field in FIELDS}
    prompt = (
        "На первом изображении обложка книги, на втором — выходные данные издательства. "
        "Точно распознай сведения только с изображений. Ничего не выдумывай. "
        "Если значение не видно или его нет, оставь пустую строку. "
        "Верни только один JSON-объект с ключами в точности как в шаблоне: "
        + json.dumps(schema, ensure_ascii=False)
    )
    payload = json.dumps({
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [{"role": "user", "content": prompt, "images": [_data_url(pair.cover), _data_url(pair.info)]}],
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": 512},
    }).encode("utf-8")
    request = urllib.request.Request(
        host.rstrip("/") + "/api/chat", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    attempts = max(1, int(os.getenv("OLLAMA_RETRIES", "3")))
    answer = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                answer = json.load(response)
            break
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            if error.code < 500 or attempt == attempts - 1:
                raise RuntimeError(f"Ошибка Ollama HTTP {error.code}: {details}") from error
        except (TimeoutError, socket.timeout) as error:
            if attempt == attempts - 1:
                raise RuntimeError(
                    f"Ollama не ответила за {timeout:g} сек. Уменьшите --workers, "
                    "увеличьте --timeout или проверьте, что модель использует GPU"
                ) from error
        except urllib.error.URLError as error:
            if attempt == attempts - 1:
                raise RuntimeError(f"Потеряно соединение с Ollama после {attempts} попыток: {error}") from error
        time.sleep(min(2 ** attempt, 10))
    if answer is None:
        raise RuntimeError("Ollama не вернула ответ")
    content = answer.get("message", {}).get("content", "")
    extracted = _extract_json(content)
    return {field: str(extracted.get(field) or "").strip() for field in FIELDS}


def process_pair(pair: PhotoPair, models: list[str], host: str, timeout: float) -> dict[str, str]:
    result = {field: "" for field in FIELDS}
    errors: list[str] = []
    successful_models = 0
    for model in models:
        try:
            candidate = query_ollama(pair, model, host, timeout)
            successful_models += 1
            for field in FIELDS:
                if not result[field] and candidate[field]:
                    result[field] = candidate[field]
        except Exception as error:  # Keep other models and books processing.
            errors.append(f"{model}: {error}")
    if errors:
        print(f"Предупреждение: {pair.cover}: {'; '.join(errors)}", file=sys.stderr)
    if not successful_models:
        raise RuntimeError(f"Не удалось обработать {pair.cover.name}: {'; '.join(errors)}")
    return {
        "Коробка": pair.box,
        **result,
        "Название файла фото обложки": pair.cover.name,
        "Название файла фото информации": pair.info.name,
    }


def write_table(rows: Iterable[dict[str, str]], output: Path) -> None:
    """Write an optional Excel copy; PostgreSQL is the primary storage."""
    rows = list(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.casefold() == ".xlsx":
        try:
            from openpyxl import Workbook
        except ImportError as error:
            raise RuntimeError("Для XLSX установите зависимость: pip install openpyxl") from error
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Книги"
        sheet.append(COLUMNS)
        for row in rows:
            sheet.append([row.get(column, "") for column in COLUMNS])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        workbook.save(output)
    else:
        raise ValueError("Формат результата должен быть .xlsx")


def process_books(
    root: Path, folders: list[str] | None, models: list[str], workers: int,
    host: str, timeout: float,
    on_progress: Callable[[dict[str, str], int, int], None] | None = None,
    on_start: Callable[[int], None] | None = None,
) -> list[dict[str, str]]:
    """Process selected boxes and return rows in deterministic photo order."""
    if workers < 1:
        raise ValueError("Количество потоков должно быть не меньше 1")
    if not models:
        raise ValueError("Укажите хотя бы одну модель")
    pairs = discover_pairs(root, folders)
    if on_start:
        on_start(len(pairs))
    rows: list[dict[str, str] | None] = [None] * len(pairs)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_pair, pair, models, host, timeout): index
            for index, pair in enumerate(pairs)
        }
        completed = 0
        for future in as_completed(futures):
            row = future.result()
            rows[futures[future]] = row
            completed += 1
            if on_progress:
                on_progress(row, completed, len(pairs))
    return [row for row in rows if row is not None]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Распознавание книг по парам фотографий через Ollama")
    parser.add_argument("root", type=Path, help="Корневая папка с папками-коробками")
    parser.add_argument("--folders", nargs="+", metavar="ПАПКА", help="Обработать только указанные папки")
    parser.add_argument("--models", default="qwen2.5vl:7b", help="Модели Ollama через запятую")
    parser.add_argument("--workers", type=int, default=1, help="Количество параллельных пар (по умолчанию: 1)")
    parser.add_argument("--output", type=Path, help="Дополнительно сохранить копию в .xlsx")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor"), help="Строка подключения PostgreSQL")
    parser.add_argument("--ollama-host", default="http://localhost:11434", help="Адрес Ollama API")
    parser.add_argument("--timeout", type=float, default=1800, help="Тайм-аут запроса в секундах")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    models = [model.strip() for model in args.models.split(",") if model.strip()]
    try:
        rows = process_books(
            args.root, args.folders, models, args.workers, args.ollama_host, args.timeout,
        )
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    from database import save_books
    try:
        job_id = save_books(args.database_url, rows, str(args.root), args.folders, models)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    print(f"Результаты сохранены в PostgreSQL, ID обработки: {job_id}")
    if args.output:
        write_table(rows, args.output)
        print(f"Копия Excel сохранена: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
