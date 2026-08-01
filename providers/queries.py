from __future__ import annotations

import re

STOP_WORDS = {
    "и", "в", "на", "с", "по", "для", "из", "к", "о", "об", "the", "a", "an", "of", "and",
    "der", "die", "das", "und", "von", "zu", "ein", "eine",
}


def normalized_title_words(title: str) -> str:
    words = re.findall(r"[\w’-]+", title.casefold(), flags=re.UNICODE)
    significant = [word for word in words if len(word) > 2 and word not in STOP_WORDS]
    return " ".join(significant[:8])


def build_queries(metadata: dict[str, str]) -> list[str]:
    author = metadata.get("Автор", "").strip()
    title = metadata.get("Название", "").strip()
    isbn = re.sub(r"[^0-9Xx]", "", metadata.get("ISBN", ""))
    publisher = metadata.get("Издательство", "").strip()
    year = metadata.get("Год", "").strip()
    candidates = []
    if isbn:
        candidates.append(isbn)
    if author and title:
        candidates.append(f"{author} {title}")
    normalized = normalized_title_words(title)
    if normalized:
        candidates.append(normalized)
    details = " ".join(value for value in (author, publisher, year) if value)
    if details:
        candidates.append(details)
    return list(dict.fromkeys(query for query in candidates if query))
