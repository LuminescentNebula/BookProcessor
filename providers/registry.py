from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable
from .base import BibliographicProvider, Evidence


def _isbn_key(evidence: Evidence) -> str:
    for isbn in evidence.isbns:
        normalized = re.sub(r"[^0-9Xx]", "", isbn).upper()
        if len(normalized) in {10, 13}:
            return "isbn:" + normalized
    return ""


def _metadata_key(evidence: Evidence) -> str:
    normalize = lambda value: re.sub(r"\W+", "", value.casefold(), flags=re.UNICODE)
    author = normalize(evidence.authors[0]) if evidence.authors else ""
    return "meta:" + "|".join((normalize(evidence.title), author, normalize(evidence.publisher), evidence.year[:4]))


class ProviderRegistry:
    def __init__(self, providers: Iterable[BibliographicProvider]):
        self.providers = list(providers)

    def search(self, metadata: dict[str, str]) -> list[Evidence]:
        deduplicated: dict[str, Evidence] = {}
        for provider in self.providers:
            try:
                found = provider.search(metadata)
            except Exception:
                continue
            for evidence in found:
                key = _isbn_key(evidence) or _metadata_key(evidence)
                if key == "meta:|||":
                    key = f"source:{evidence.source}:{evidence.source_url}"
                if key not in deduplicated:
                    deduplicated[key] = evidence
                    continue
                current = deduplicated[key]
                references = tuple(dict.fromkeys(current.alternate_sources + ((evidence.source, evidence.source_url),)))
                deduplicated[key] = replace(current, alternate_sources=references)
        return list(deduplicated.values())

    def health(self) -> list[dict]:
        return [provider.health() for provider in self.providers]
