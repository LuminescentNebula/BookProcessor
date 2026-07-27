import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from book_processor import COLUMNS, _extract_json, discover_pairs, process_books, write_table


class BookProcessorTests(unittest.TestCase):
    def test_discovery_uses_natural_order_and_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            box = root / "box"
            box.mkdir()
            for name in ("10.jpg", "2.jpg", "1.jpg", "3.jpg", "ignore.txt"):
                (box / name).touch()
            pairs = discover_pairs(root, None)
            self.assertEqual([(p.cover.name, p.info.name) for p in pairs], [("1.jpg", "2.jpg"), ("3.jpg", "10.jpg")])

    def test_json_markdown_is_accepted(self):
        self.assertEqual(_extract_json('```json\n{"Автор": "Пушкин"}\n```')["Автор"], "Пушкин")

    def test_rejects_csv_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "books.csv"
            with self.assertRaisesRegex(ValueError, "xlsx"):
                write_table([{"Коробка": "1", "Автор": ""}], output)

    def test_process_books_validates_workers(self):
        with self.assertRaisesRegex(ValueError, "потоков"):
            process_books(Path("."), None, ["model"], 0, "http://localhost", 1)

    def test_process_books_reports_live_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            box = Path(directory) / "box"
            box.mkdir()
            (box / "1.jpg").touch()
            (box / "2.jpg").touch()
            starts, updates = [], []
            row = {column: "" for column in COLUMNS}
            with patch("book_processor.process_pair", return_value=row):
                process_books(
                    Path(directory), None, ["model"], 1, "http://localhost", 1,
                    lambda result, completed, total: updates.append((completed, total)),
                    starts.append,
                )
            self.assertEqual(starts, [1])
            self.assertEqual(updates, [(1, 1)])


if __name__ == "__main__":
    unittest.main()
