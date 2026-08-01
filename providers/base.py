from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Evidence:
    source: str
    source_url: str
    title: str = ""
    authors: tuple[str, ...] = ()
    publisher: str = ""
    year: str = ""
    isbns: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    alternate_sources: tuple[tuple[str, str], ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class BibliographicProvider(Protocol):
    name: str

    def search(self, metadata: dict[str, str]) -> list[Evidence]: ...
    def health(self) -> dict[str, Any]: ...


class JsonProvider:
    name = "provider"

    def __init__(self, timeout: float, retries: int, min_interval: float):
        self.timeout = timeout
        self.retries = max(1, retries)
        self.min_interval = max(0.0, min_interval)
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._status = {"status": "unknown", "message": "Запросы ещё не выполнялись", "last_checked": None}

    def _rate_limit(self) -> None:
        with self._lock:
            delay = self.min_interval - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()

    def get_json(self, url: str) -> dict[str, Any]:
        error: Exception | None = None
        for attempt in range(self.retries):
            self._rate_limit()
            request = urllib.request.Request(url, headers={"User-Agent": "BookProcessor/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    result = json.load(response)
                self._status = {"status": "available", "message": "Доступен", "last_checked": time.time()}
                return result
            except (OSError, ValueError, urllib.error.URLError) as exception:
                error = exception
                if attempt + 1 < self.retries:
                    time.sleep(min(2 ** attempt, 5))
        self._status = {"status": "unavailable", "message": str(error), "last_checked": time.time()}
        raise RuntimeError(f"{self.name}: {error}") from error

    def health(self) -> dict[str, Any]:
        return {"name": self.name, **self._status}
