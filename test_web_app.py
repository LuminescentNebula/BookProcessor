import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import DEFAULT_SETTINGS, create_app
from app.folder_watcher import folder_names, folder_signature


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.database = MagicMock()
        self.database.load_settings.return_value = dict(DEFAULT_SETTINGS)
        self.database.list_books.return_value = []
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
        self.assertIn(b"/api/books/1/image/info", self.client.get("/library").data)
        self.assertIn("Таблица обработанных книг".encode(), self.client.get("/books").data)

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
