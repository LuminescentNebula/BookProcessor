import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from book_processor import COLUMNS, PhotoPair, _extract_json, discover_pairs, normalize_metadata, process_books, process_pair, query_ollama, write_table


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

    def test_ollama_timeout_has_actionable_message(self):
        pair = PhotoPair("box", Path("cover.jpg"), Path("info.jpg"))
        with patch.dict(os.environ, {"OLLAMA_RETRIES": "1"}), patch("book_processor._data_url", return_value="image"), patch(
            "book_processor.urllib.request.urlopen", side_effect=TimeoutError,
        ):
            with self.assertRaisesRegex(RuntimeError, "Уменьшите --workers"):
                query_ollama(pair, "model", "http://ollama:11434", 10)

    def test_pair_fails_when_every_model_times_out(self):
        pair = PhotoPair("box", Path("cover.jpg"), Path("info.jpg"))
        with patch("book_processor.query_ollama", side_effect=RuntimeError("timeout")):
            with self.assertRaisesRegex(RuntimeError, "Не удалось обработать"):
                process_pair(pair, ["model-a", "model-b"], "http://ollama:11434", 10)

    def test_normalization_uses_separate_text_model(self):
        response = {"message": {"content": '{"Автор":"Лев Николаевич Толстой","Название":"Война и мир","Тираж":"10000"}'}}
        with patch("book_processor._ollama_chat", return_value=response) as chat:
            result = normalize_metadata({"Автор": "Л. ТОЛСТОЙ", "Название": "ВОЙНА И МИР"}, [{"isbn": ["123"]}], "text-model", "http://ollama", 30)
        self.assertEqual(result["Автор"], "Лев Николаевич Толстой")
        self.assertEqual(chat.call_args.args[1]["model"], "text-model")

    def test_print_run_is_saved_as_digits_only(self):
        pair = PhotoPair("box", Path("cover.jpg"), Path("info.jpg"))
        raw = {field: "" for field in COLUMNS if field not in ("Коробка", "Название файла фото обложки", "Название файла фото информации")}
        raw["Тираж"] = "10 000 экз."
        with patch("book_processor.query_ollama", return_value=raw):
            result = process_pair(pair, ["vision"], "http://ollama", 30)
        self.assertEqual(result["Тираж"], "10000")


if __name__ == "__main__":
    unittest.main()
