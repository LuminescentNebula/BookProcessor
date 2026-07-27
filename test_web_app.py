import tempfile
import time
import unittest
from pathlib import Path

from web_app import app, folder_names, jobs, jobs_lock


class WebAppTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
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


if __name__ == "__main__":
    unittest.main()
