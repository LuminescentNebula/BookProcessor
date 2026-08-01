import unittest
import urllib.error
from unittest.mock import patch

from providers import Evidence, ProviderRegistry, create_registry
from providers.crossref import CrossrefProvider
from providers.google_books import GoogleBooksProvider
from providers.open_library import OpenLibraryProvider
from providers.library_of_congress import LibraryOfCongressProvider
from providers.queries import build_queries


class FakeProvider:
    def __init__(self, name, results=None, error=None):
        self.name, self.results, self.error = name, results or [], error

    def search(self, metadata):
        if self.error:
            raise self.error
        return self.results

    def health(self):
        return {"name": self.name, "status": "unavailable" if self.error else "available"}


class ProviderTests(unittest.TestCase):
    def test_default_registry_contains_multiple_independent_catalogues(self):
        registry = create_registry({})
        self.assertEqual(
            [provider.name for provider in registry.providers],
            ["Open Library", "Google Books", "Crossref", "Library of Congress"],
        )

    def test_query_variants_cover_isbn_metadata_and_title_words(self):
        queries = build_queries({
            "ISBN": "978-5-123-45678-9", "Автор": "Лев Толстой",
            "Название": "Война и мир", "Издательство": "Наука", "Год": "1980",
        })
        self.assertIn("9785123456789", queries)
        self.assertIn("Лев Толстой Война и мир", queries)
        self.assertIn("война мир", queries)
        self.assertIn("Лев Толстой Наука 1980", queries)

    def test_partial_provider_failure_keeps_other_results(self):
        evidence = Evidence(source="Open Library", source_url="https://openlibrary.org/x", title="Книга", authors=("Автор",), isbns=("123456789X",))
        registry = ProviderRegistry([
            FakeProvider("broken", error=RuntimeError("offline")),
            FakeProvider("working", results=[evidence]),
        ])
        self.assertEqual(registry.search({"Название": "Книга"}), [evidence])
        self.assertEqual([item["status"] for item in registry.health()], ["unavailable", "available"])

    def test_duplicates_preserve_alternate_source(self):
        first = Evidence(source="Open Library", source_url="https://openlibrary.org/x", title="Book", isbns=("9781234567890",))
        second = Evidence(source="Google Books", source_url="https://books.google/x", title="Book", isbns=("978-1-234-56789-0",))
        result = ProviderRegistry([FakeProvider("one", [first]), FakeProvider("two", [second])]).search({})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].alternate_sources, (("Google Books", "https://books.google/x"),))

    def test_official_providers_map_source_urls(self):
        open_library = OpenLibraryProvider(timeout=3, retries=1, min_interval=0)
        google = GoogleBooksProvider(timeout=4, retries=1, min_interval=0)
        with patch.object(open_library, "get_json", return_value={"docs": [{"key": "/works/OL1W", "title": "Book", "isbn": ["1"]}]}):
            open_result = open_library.search({"Название": "Book"})[0]
        with patch.object(google, "get_json", return_value={"items": [{"selfLink": "https://google/item", "volumeInfo": {"title": "Book"}}]}):
            google_result = google.search({"Название": "Book"})[0]
        self.assertEqual(open_result.source_url, "https://openlibrary.org/works/OL1W")
        self.assertEqual(google_result.source_url, "https://google/item")
        self.assertEqual((open_library.timeout, google.timeout), (3, 4))

    def test_crossref_maps_book_metadata(self):
        provider = CrossrefProvider(timeout=3, retries=1, min_interval=0)
        payload = {"message": {"items": [{
            "DOI": "10.1/book", "URL": "https://doi.org/10.1/book", "title": ["Книга"],
            "author": [{"given": "Иван", "family": "Иванов"}], "publisher": "Наука",
            "published": {"date-parts": [[1999]]}, "ISBN": ["9785000000001"],
            "language": "ru", "subject": ["История"],
        }]}}
        with patch.object(provider, "get_json", return_value=payload):
            result = provider.search({"Название": "Книга"})[0]
        self.assertEqual((result.title, result.authors, result.year), ("Книга", ("Иван Иванов",), "1999"))
        self.assertEqual(result.source_url, "https://doi.org/10.1/book")

    def test_library_of_congress_maps_catalogue_record(self):
        provider = LibraryOfCongressProvider(timeout=3, retries=1, min_interval=0)
        payload = {"results": [{"id": "https://www.loc.gov/item/1/", "title": "Buch",
            "contributor": ["Mann, Thomas"], "publisher": ["Fischer"], "date": "1924",
            "isbn": ["978-3-10-000000-0"], "language": ["German"], "subject": ["Novel"]}]}
        with patch.object(provider, "get_json", return_value=payload):
            result = provider.search({"Название": "Buch"})[0]
        self.assertEqual(result.source, "Library of Congress")
        self.assertEqual(result.isbns, ("9783100000000",))
        self.assertEqual(result.source_url, "https://www.loc.gov/item/1/")

    def test_provider_retries_and_reports_unavailable(self):
        provider = OpenLibraryProvider(timeout=2, retries=2, min_interval=0)
        with patch("providers.base.urllib.request.urlopen", side_effect=urllib.error.URLError("offline")) as request, patch("providers.base.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "Open Library"):
                provider.get_json("https://openlibrary.org/search.json?q=x")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(provider.health()["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
