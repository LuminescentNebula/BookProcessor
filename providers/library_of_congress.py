from __future__ import annotations

import re
import urllib.parse

from .base import Evidence, JsonProvider
from .queries import build_queries


class LibraryOfCongressProvider(JsonProvider):
    """Official Library of Congress JSON API provider for book catalogue records."""

    name = "Library of Congress"
    endpoint = "https://www.loc.gov/books/"

    @staticmethod
    def _list(value) -> list:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def search(self, metadata: dict[str, str]) -> list[Evidence]:
        results = []
        for query in build_queries(metadata):
            url = self.endpoint + "?" + urllib.parse.urlencode({"q": query, "fo": "json", "c": 5})
            data = self.get_json(url)
            for item in data.get("results", [])[:5]:
                date = str(item.get("date") or "")
                identifiers = self._list(item.get("isbn"))
                isbns = tuple(filter(None, (re.sub(r"[^0-9Xx]", "", value) for value in identifiers)))
                contributors = self._list(item.get("contributor"))
                publishers = self._list(item.get("publisher"))
                languages = self._list(item.get("language"))
                subjects = self._list(item.get("subject"))
                source_urls = self._list(item.get("id") or item.get("url"))
                results.append(Evidence(
                    source=self.name,
                    source_url=source_urls[0] if source_urls else url,
                    title=item.get("title", ""),
                    authors=tuple(contributors[:5]),
                    publisher=publishers[0] if publishers else "",
                    year=date[:4] if date[:4].isdigit() else "",
                    isbns=isbns[:10],
                    languages=tuple(languages[:10]),
                    subjects=tuple(subjects[:10]),
                    raw=item,
                ))
        return results
