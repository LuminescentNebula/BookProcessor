from .base import BibliographicProvider, Evidence
from .google_books import GoogleBooksProvider
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
    ])


__all__ = ["BibliographicProvider", "Evidence", "ProviderRegistry", "create_registry"]
