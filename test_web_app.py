import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from web_app import DEFAULT_SETTINGS, app, folder_names, folder_signature, jobs, jobs_lock


class WebAppTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session.update(user_id=1, username="admin", role="admin")
        with jobs_lock:
            jobs.clear()

    def test_index_contains_form(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Обработка фотографий книг".encode(), response.data)

    def test_folders_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "box-2").mkdir()
            response = self.client.get("/api/folders", query_string={"root": directory})
            self.assertEqual(response.json, {"folders": ["box-2"]})

    def test_folder_names_rejects_missing_root(self):
        with self.assertRaisesRegex(ValueError, "не найдена"):
            folder_names(Path("/definitely/missing"))

    def test_job_status_returns_live_rows(self):
        with jobs_lock:
            jobs["abc"] = {
                "status": "processing", "completed": 1, "total": 2,
                "rows": [{"Название": "Книга"}], "error": None,
                "database_job_id": None, "started_at": time.time() - 5,
                "finished_at": None,
            }
        response = self.client.get("/api/jobs/abc")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["rows"][0]["Название"], "Книга")
        self.assertGreaterEqual(response.json["elapsed_seconds"], 5)

    def test_unknown_job_returns_404(self):
        self.assertEqual(self.client.get("/api/jobs/missing").status_code, 404)

    def test_health_reports_degraded_services(self):
        with patch("web_app.application_settings", return_value=DEFAULT_SETTINGS), patch("web_app.check_database", return_value=(False, "PostgreSQL недоступна")), patch(
            "web_app.check_ollama", return_value=(True, "Ollama доступна"),
        ):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "degraded")
        self.assertFalse(response.json["database"]["ok"])

    def test_folder_signature_ignores_non_images(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "1.jpg").write_bytes(b"photo")
            before = folder_signature(folder)
            (folder / "notes.txt").write_text("not a photo")
            self.assertEqual(folder_signature(folder), before)

    def test_library_page_renders_book_cards(self):
        book = {"id": 1, "title": "Книга", "author": "Автор", "genre": "Роман", "publisher": "Издатель", "publication_year": "2024", "isbn": "123"}
        known = {"author": ["Автор"], "publisher": ["Издатель"], "genre": ["Роман"]}
        with patch("web_app.list_books", return_value=[book]), patch("web_app.known_book_values", return_value=known):
            response = self.client.get("/library")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Библиотека обработанных книг".encode(), response.data)
        self.assertIn(b"/api/books/1/image/info", response.data)

    def test_books_page_has_sort_links_and_filters(self):
        known = {"author": [], "publisher": [], "genre": []}
        with patch("web_app.list_books", return_value=[]), patch("web_app.known_book_values", return_value=known):
            response = self.client.get("/books", query_string={"sort": "author", "direction": "asc"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Таблица обработанных книг".encode(), response.data)
        self.assertIn("Из известных значений".encode(), response.data)

    def test_settings_page_saves_values(self):
        values = {**DEFAULT_SETTINGS, "BOOK_WORKERS": "3"}
        with patch("web_app.save_settings") as save:
            response = self.client.post("/settings", data=values)
        self.assertEqual(response.status_code, 302)
        save.assert_called_once()
        self.assertEqual(save.call_args.args[1]["BOOK_WORKERS"], "3")

    def test_book_can_be_edited(self):
        with patch("web_app.update_book", return_value=True) as update:
            response = self.client.patch("/api/books/7", json={"title": "Новое название"})
        self.assertEqual(response.status_code, 200)
        update.assert_called_once_with(ANY, 7, {"title": "Новое название"})

    def test_viewer_can_browse_but_cannot_edit_or_open_settings(self):
        with self.client.session_transaction() as session:
            session.update(user_id=2, username="reader", role="viewer")
        known = {"author": [], "publisher": [], "genre": []}
        with patch("web_app.list_books", return_value=[]), patch("web_app.known_book_values", return_value=known):
            self.assertEqual(self.client.get("/library").status_code, 200)
        self.assertEqual(self.client.get("/settings").status_code, 403)
        self.assertEqual(self.client.patch("/api/books/7", json={"title": "Нет"}).status_code, 403)

    def test_anonymous_user_is_redirected_to_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get("/library")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

    def test_admin_can_queue_selected_books_for_normalization(self):
        selected = [{"id": 7, "box": "box", "title": "Книга"}]
        with patch("web_app.application_settings", return_value=DEFAULT_SETTINGS), patch(
            "web_app.get_books", return_value=selected,
        ), patch("web_app.job_executor.submit") as submit:
            response = self.client.post("/api/books/normalize", json={"book_ids": [7]})
        self.assertEqual(response.status_code, 202)
        self.assertIn("job_id", response.json)
        submit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
