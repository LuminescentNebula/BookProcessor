#!/usr/bin/env python3
"""Small local web interface for BookProcessor."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from book_processor import natural_key, process_books
from database import save_books

app = Flask(__name__)
app.config["OLLAMA_HOST"] = os.getenv("OLLAMA_HOST", "http://localhost:11434")


def folder_names(root: Path) -> list[str]:
    if not root.is_dir():
        raise ValueError("Корневая папка не найдена")
    return [path.name for path in sorted((p for p in root.iterdir() if p.is_dir()), key=natural_key)]


@app.get("/")
def index():
    return render_template("index.html", ollama_host=app.config["OLLAMA_HOST"])


@app.get("/api/folders")
def folders():
    try:
        return jsonify({"folders": folder_names(Path(request.args.get("root", "")))})
    except (OSError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.post("/process")
def process():
    try:
        root = Path(request.form["root"]).expanduser()
        models = [item.strip() for item in request.form["models"].split(",") if item.strip()]
        selected = request.form.getlist("folders") or None
        workers = int(request.form["workers"])
        timeout = float(request.form["timeout"])
        rows = process_books(root, selected, models, workers, request.form["ollama_host"], timeout)
        job_id = save_books(
            os.getenv("DATABASE_URL", "postgresql://bookprocessor:bookprocessor@localhost:5432/bookprocessor"),
            rows, str(root), selected, models,
        )
        return render_template("index.html", success=f"Сохранено книг: {len(rows)}. ID обработки: {job_id}", ollama_host=app.config["OLLAMA_HOST"])
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        return render_template("index.html", error=str(error), ollama_host=app.config["OLLAMA_HOST"]), 400


def main() -> None:
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
