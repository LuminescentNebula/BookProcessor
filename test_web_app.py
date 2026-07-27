import tempfile
import unittest
from pathlib import Path

from web_app import app, folder_names


class WebAppTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

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


if __name__ == "__main__":
    unittest.main()
