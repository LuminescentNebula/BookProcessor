from __future__ import annotations

import urllib.parse
from .base import Evidence, JsonProvider
from .queries import build_queries


class GoogleBooksProvider(JsonProvider):
    name = "Google Books"
    endpoint = "https://www.googleapis.com/books/v1/volumes"

    def __init__(self, timeout: float, retries: int, min_interval: float, api_key: str = ""):
        super().__init__(timeout, retries, min_interval)
        self.api_key = api_key

    def search(self, metadata: dict[str, str]) -> list[Evidence]:
        results = []
        for query in build_queries(metadata):
            parameters = {"q": query, "maxResults": 5, "printType": "books"}
            if self.api_key:
                parameters["key"] = self.api_key
            url = self.endpoint + "?" + urllib.parse.urlencode(parameters)
            data = self.get_json(url)
            for item in data.get("items", [])[:5]:
                info = item.get("volumeInfo", {})
                identifiers = tuple(value.get("identifier", "") for value in info.get("industryIdentifiers", []) if value.get("identifier"))
                published = str(info.get("publishedDate", ""))
                results.append(Evidence(
                    source=self.name,
                    source_url=info.get("infoLink") or item.get("selfLink") or url,
                    title=info.get("title", ""),
                    authors=tuple(info.get("authors", [])[:5]),
                    publisher=info.get("publisher", ""),
                    year=published[:4],
                    isbns=identifiers,
                    languages=(info.get("language", ""),) if info.get("language") else (),
                    subjects=tuple(info.get("categories", [])[:10]),
                    raw=item,
                ))
        return results
