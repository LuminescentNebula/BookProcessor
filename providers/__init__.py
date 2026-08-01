from .base import BibliographicProvider, Evidence
from .crossref import CrossrefProvider
from .google_books import GoogleBooksProvider
from .library_of_congress import LibraryOfCongressProvider
from .open_library import OpenLibraryProvider
from .registry import ProviderRegistry


def create_registry(config) -> ProviderRegistry:
    return ProviderRegistry([
        OpenLibraryProvider(
            timeout=float(config.get("OPEN_LIBRARY_TIMEOUT", 10)),
            retries=int(config.get("OPEN_LIBRARY_RETRIES", 2)),
            min_interval=float(config.get("OPEN_LIBRARY_MIN_INTERVAL", 0.5)),
        ),
        GoogleBooksProvider(
            timeout=float(config.get("GOOGLE_BOOKS_TIMEOUT", 10)),
            retries=int(config.get("GOOGLE_BOOKS_RETRIES", 2)),
            min_interval=float(config.get("GOOGLE_BOOKS_MIN_INTERVAL", 0.5)),
            api_key=str(config.get("GOOGLE_BOOKS_API_KEY", "")),
        ),
        CrossrefProvider(
            timeout=float(config.get("CROSSREF_TIMEOUT", 10)),
            retries=int(config.get("CROSSREF_RETRIES", 2)),
            min_interval=float(config.get("CROSSREF_MIN_INTERVAL", 0.5)),
            mailto=str(config.get("CROSSREF_MAILTO", "")),
        ),
        LibraryOfCongressProvider(
            timeout=float(config.get("LOC_TIMEOUT", 10)),
            retries=int(config.get("LOC_RETRIES", 2)),
            min_interval=float(config.get("LOC_MIN_INTERVAL", 0.5)),
        ),
    ])


__all__ = ["BibliographicProvider", "Evidence", "ProviderRegistry", "create_registry"]
