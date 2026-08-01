from __future__ import annotations

import urllib.parse

from .base import Evidence, JsonProvider
from .queries import build_queries


class CrossrefProvider(JsonProvider):
    """Official Crossref REST API provider for registered book metadata."""

    name = "Crossref"
    endpoint = "https://api.crossref.org/works"

    def __init__(self, timeout: float, retries: int, min_interval: float, mailto: str = ""):
        super().__init__(timeout, retries, min_interval)
        self.mailto = mailto

    def search(self, metadata: dict[str, str]) -> list[Evidence]:
        results = []
        for query in build_queries(metadata):
            parameters = {"query.bibliographic": query, "filter": "type:book", "rows": 5}
            if self.mailto:
                parameters["mailto"] = self.mailto
            url = self.endpoint + "?" + urllib.parse.urlencode(parameters)
            data = self.get_json(url)
            for item in data.get("message", {}).get("items", [])[:5]:
                titles = item.get("title") or []
                if isinstance(titles, str):
                    titles = [titles]
                authors = tuple(
                    " ".join(part for part in (author.get("given", ""), author.get("family", "")) if part).strip()
                    for author in item.get("author", [])[:5]
                )
                date_parts = (item.get("published-print") or item.get("published") or {}).get("date-parts", [])
                year = str(date_parts[0][0]) if date_parts and date_parts[0] else ""
                doi = item.get("DOI", "")
                results.append(Evidence(
                    source=self.name,
                    source_url=item.get("URL") or (f"https://doi.org/{doi}" if doi else url),
                    title=titles[0] if titles else "",
                    authors=tuple(author for author in authors if author),
                    publisher=item.get("publisher", ""),
                    year=year,
                    isbns=tuple(item.get("ISBN", [])[:10]),
                    languages=(item.get("language", ""),) if item.get("language") else (),
                    subjects=tuple(item.get("subject", [])[:10]),
                    raw=item,
                ))
        return results
