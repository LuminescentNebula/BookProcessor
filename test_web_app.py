import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from app import DEFAULT_SETTINGS, create_app
from app.folder_watcher import folder_names, folder_signature


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.database = MagicMock()
        self.database.load_settings.return_value = dict(DEFAULT_SETTINGS)
        self.database.list_books.return_value = []
        self.database.count_books.return_value = 0
        self.database.known_book_values.return_value = {"author": [], "publisher": [], "genre": []}
        self.database.check_database.return_value = (True, "PostgreSQL доступна")
        self.services = SimpleNamespace(
            database=self.database,
            process_books=MagicMock(return_value=[]),
            normalize_metadata=MagicMock(),
            internet_search=MagicMock(),
        )
        self.services.internet_search.health.return_value = []
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test", "SERVICES": self.services, "START_FOLDER_WATCHER": False, "BOOTSTRAP_ADMIN": False})
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session.update(user_id=1, username="admin", role="admin")

    def test_factory_has_isolated_job_state(self):
        other = create_app({"TESTING": True, "SERVICES": self.services})
        self.app.extensions["job_state"].jobs["one"] = {}
        self.assertEqual(other.extensions["job_state"].jobs, {})

    def test_index(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Обработка фотографий книг".encode(), response.data)

    def test_wsgi_factory_does_not_start_folder_watcher(self):
        self.assertNotIn("folder_watcher", self.app.extensions)

    def test_web_health_is_separate_and_does_not_require_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ready")

    def test_folders_endpoint_and_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "box-2").mkdir()
            self.assertEqual(folder_names(root), ["box-2"])
            self.assertEqual(self.client.get("/api/folders", query_string={"root": directory}).json, {"folders": ["box-2"]})
        with self.assertRaisesRegex(ValueError, "не найдена"):
            folder_names(Path("/definitely/missing"))

    def test_folder_signature_ignores_non_images(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory); (folder / "1.jpg").write_bytes(b"photo")
            before = folder_signature(folder); (folder / "notes.txt").write_text("note")
            self.assertEqual(folder_signature(folder), before)

    def test_job_status(self):
        state = self.app.extensions["job_state"]
        state.jobs["abc"] = {"status":"processing","completed":1,"total":2,"rows":[{"Название":"Книга"}],"error":None,"database_job_id":None,"started_at":time.time()-5,"finished_at":None}
        response = self.client.get("/api/jobs/abc")
        self.assertEqual(response.json["rows"][0]["Название"], "Книга")
        self.assertGreaterEqual(response.json["elapsed_seconds"], 5)

    def test_health_degraded(self):
        self.database.check_database.return_value = (False, "PostgreSQL недоступна")
        with patch("app.health.check_ollama", return_value=(True, "Ollama доступна")):
            response = self.client.get("/api/health")
        self.assertEqual(response.json["status"], "degraded")

    def test_health_contains_independent_provider_states(self):
        self.services.internet_search.health.return_value = [
            {"name": "Open Library", "status": "available", "message": "Доступен"},
            {"name": "Google Books", "status": "unavailable", "message": "offline"},
        ]
        with patch("app.health.check_ollama", return_value=(True, "Ollama доступна")):
            response = self.client.get("/api/health")
        self.assertEqual([item["status"] for item in response.json["providers"]], ["available", "unavailable"])

    def test_library_and_table(self):
        book = {"id":1,"box":"b","title":"Книга","author":"Автор","genre":"Роман","publisher":"Издатель","publication_year":"2024","isbn":"123"}
        self.database.list_books.return_value = [book]
        self.database.count_books.return_value = 1
        self.assertIn(b"/api/books/1/image/info", self.client.get("/library").data)
        self.assertIn("Таблица обработанных книг".encode(), self.client.get("/books").data)

    def test_book_pages_first_middle_last_and_preserve_filters(self):
        self.app.config["BOOK_PAGE_SIZE"] = 2
        self.database.count_books.return_value = 5
        for requested, expected_page, expected_offset in ((1, 1, 0), (2, 2, 2), (3, 3, 4)):
            response = self.client.get("/books", query_string={"page": requested, "author": "Толстой", "sort": "title", "direction": "asc"})
            self.assertEqual(response.status_code, 200)
            self.assertIn(f"Страница {expected_page} из 3".encode(), response.data)
            self.database.list_books.assert_called_with(
                self.app.config["DATABASE_URL"], ANY, "title", "asc", 2, expected_offset
            )
            self.assertIn(b"author=%D0%A2%D0%BE%D0%BB%D1%81%D1%82%D0%BE%D0%B9", response.data)

    def test_invalid_book_pages_are_clamped(self):
        self.app.config["BOOK_PAGE_SIZE"] = 2
        self.database.count_books.return_value = 5
        self.assertIn("Страница 1 из 3".encode(), self.client.get("/library?page=oops").data)
        self.assertIn("Страница 1 из 3".encode(), self.client.get("/library?page=-4").data)
        self.assertIn("Страница 3 из 3".encode(), self.client.get("/library?page=99").data)

    def test_job_list_is_summary_and_job_rows_are_paginated(self):
        state = self.app.extensions["job_state"]
        state.jobs["many"] = {"status": "completed", "completed": 3, "total": 3,
            "rows": [{"Название": str(number)} for number in range(3)], "error": None,
            "database_job_id": 1, "started_at": time.time() - 2, "finished_at": time.time()}
        listing = self.client.get("/api/jobs").json
        self.assertEqual(listing["total_jobs"], 1)
        self.assertNotIn("rows", listing["jobs"][0])
        self.assertEqual(listing["jobs"][0]["row_count"], 3)
        middle = self.client.get("/api/jobs/many?page=2&per_page=1").json
        self.assertEqual((middle["page"], middle["total_pages"], middle["total_rows"]), (2, 3, 3))
        self.assertEqual(middle["rows"], [{"Название": "1"}])
        self.assertEqual(self.client.get("/api/jobs/many?page=0").status_code, 400)

    def test_settings_save(self):
        values = {**DEFAULT_SETTINGS, "BOOK_WORKERS": "3"}
        response = self.client.post("/settings", data=values)
        self.assertEqual(response.status_code, 302)
        self.database.save_settings.assert_called_once()

    def test_book_edit(self):
        self.database.update_book.return_value = True
        self.assertEqual(self.client.patch("/api/books/7", json={"title":"Новое"}).status_code, 200)
        self.database.update_book.assert_called_once()

    def test_permissions(self):
        with self.client.session_transaction() as session:
            session.update(user_id=2, username="reader", role="viewer")
        self.assertEqual(self.client.get("/library").status_code, 200)
        self.assertEqual(self.client.get("/settings").status_code, 403)
        self.assertEqual(self.client.patch("/api/books/7", json={"title":"Нет"}).status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertIn("/login", self.client.get("/library").location)

    def test_normalization_is_queued_through_injected_services(self):
        self.database.get_books.return_value = [{"id":7,"box":"box","title":"Книга"}]
        state = self.app.extensions["job_state"]
        with patch.object(state.executor, "submit") as submit:
            response = self.client.post("/api/books/normalize", json={"book_ids":[7]})
        self.assertEqual(response.status_code, 202)
        submit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
