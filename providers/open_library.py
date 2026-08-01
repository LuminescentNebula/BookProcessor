from __future__ import annotations

import urllib.parse
from .base import Evidence, JsonProvider
from .queries import build_queries


class OpenLibraryProvider(JsonProvider):
    name = "Open Library"
    endpoint = "https://openlibrary.org/search.json"

    def search(self, metadata: dict[str, str]) -> list[Evidence]:
        results = []
        fields = "key,title,author_name,first_publish_year,publisher,isbn,language,subject"
        for query in build_queries(metadata):
            url = self.endpoint + "?" + urllib.parse.urlencode({"q": query, "limit": 5, "fields": fields})
            data = self.get_json(url)
            for item in data.get("docs", [])[:5]:
                key = item.get("key", "")
                results.append(Evidence(
                    source=self.name,
                    source_url=f"https://openlibrary.org{key}" if key else url,
                    title=item.get("title", ""),
                    authors=tuple(item.get("author_name", [])[:5]),
                    publisher=(item.get("publisher") or [""])[0],
                    year=str(item.get("first_publish_year") or ""),
                    isbns=tuple(item.get("isbn", [])[:10]),
                    languages=tuple(item.get("language", [])[:10]),
                    subjects=tuple(item.get("subject", [])[:10]),
                    raw=item,
                ))
        return results
