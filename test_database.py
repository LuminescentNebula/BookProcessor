import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from database import DatabaseUnavailable, INSERT_BOOK, count_books, get_books, list_books, load_settings, save_books, save_settings, source_was_processed, update_book


class DatabaseTests(unittest.TestCase):
    def test_count_and_page_use_identical_filters_and_limit_is_capped(self):
        columns = [MagicMock()]
        columns[0].name = "id"
        cursor = MagicMock(description=columns)
        cursor.fetchall.return_value = []
        connection = MagicMock(); connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        count_result = MagicMock(); count_result.fetchone.return_value = (7,)
        connection.execute.return_value = count_result
        psycopg = types.SimpleNamespace(connect=MagicMock(return_value=connection), OperationalError=Exception)
        filters = {"author": "Толстой", "publisher_select": "Наука"}
        with patch("database.importlib.import_module", return_value=psycopg):
            self.assertEqual(count_books("db", filters), 7)
            list_books("db", filters, limit=999, offset=-3)
        count_query, count_values = connection.execute.call_args.args
        list_query, list_values = cursor.execute.call_args.args
        self.assertIn("COUNT(*)", count_query)
        self.assertIn("b.author ILIKE %s", count_query)
        self.assertIn("b.publisher = %s", count_query)
        self.assertEqual(count_values, ["%Толстой%", "Наука"])
        self.assertIn("LIMIT %s OFFSET %s", list_query)
        self.assertEqual(list_values, ["%Толстой%", "Наука", 100, 0])

    def test_save_books_creates_job_and_inserts_rows(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (42,)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        psycopg = types.SimpleNamespace(connect=MagicMock(return_value=connection))
        row = {
            "Коробка": "box-1", "Автор": "Автор", "Название": "Книга", "Год": "2024",
            "Издательство": "Издатель", "Тираж": "1000", "Язык": "русский", "ISBN": "123",
            "Жанр": "Роман",
            "Название файла фото обложки": "1.jpg", "Название файла фото информации": "2.jpg",
        }
        with patch.dict(sys.modules, {"psycopg": psycopg}):
            job_id = save_books("postgresql://test", [row], "/photos", ["box-1"], ["vision"])
        self.assertEqual(job_id, 42)
        psycopg.connect.assert_called_once_with("postgresql://test")
        cursor.executemany.assert_called_once()
        self.assertEqual(cursor.executemany.call_args.args[0], INSERT_BOOK)
        self.assertEqual(cursor.executemany.call_args.args[1][0][1:4], ("box-1", "Автор", "Книга"))
        self.assertEqual(cursor.executemany.call_args.args[1][0][9], "Роман")

    def test_connection_loss_has_specific_error(self):
        class OperationalError(Exception):
            pass

        psycopg = types.SimpleNamespace(
            connect=MagicMock(side_effect=OperationalError("connection lost")),
            OperationalError=OperationalError,
            Error=Exception,
        )
        with patch("database.importlib.import_module", return_value=psycopg):
            with self.assertRaisesRegex(DatabaseUnavailable, "временно недоступна"):
                save_books("postgresql://test", [], "/photos", None, ["vision"])

    def test_processed_folder_signature_is_detected(self):
        result = MagicMock()
        result.fetchone.return_value = (1,)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.side_effect = [MagicMock(), result]
        psycopg = types.SimpleNamespace(
            connect=MagicMock(return_value=connection),
            OperationalError=Exception,
        )
        with patch("database.importlib.import_module", return_value=psycopg):
            self.assertTrue(source_was_processed("postgresql://test", "/photos/box", "signature"))
        connection.execute.assert_called_with(
            "SELECT 1 FROM processing_jobs WHERE photo_root = %s AND source_signature = %s LIMIT 1",
            ("/photos/box", "signature"),
        )

    def test_settings_are_merged_with_defaults(self):
        stored = MagicMock()
        stored.fetchall.return_value = [("BOOK_WORKERS", "4")]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.side_effect = [MagicMock(), stored]
        psycopg = types.SimpleNamespace(connect=MagicMock(return_value=connection), OperationalError=Exception)
        with patch("database.importlib.import_module", return_value=psycopg):
            result = load_settings("postgresql://test", {"BOOK_WORKERS": "1", "OLLAMA_HOST": "http://ollama"})
        self.assertEqual(result, {"BOOK_WORKERS": "4", "OLLAMA_HOST": "http://ollama"})

    def test_settings_are_upserted(self):
        cursor = MagicMock()
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        psycopg = types.SimpleNamespace(connect=MagicMock(return_value=connection), OperationalError=Exception)
        with patch("database.importlib.import_module", return_value=psycopg):
            save_settings("postgresql://test", {"BOOK_WORKERS": "2"})
        cursor.executemany.assert_called_once()
        self.assertEqual(cursor.executemany.call_args.args[1], [("BOOK_WORKERS", "2")])

    def test_update_book_uses_only_allowlisted_fields(self):
        result = MagicMock(rowcount=1)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value = result
        psycopg = types.SimpleNamespace(connect=MagicMock(return_value=connection), OperationalError=Exception)
        with patch("database.importlib.import_module", return_value=psycopg):
            updated = update_book("postgresql://test", 7, {"title": "Новое", "not_a_column": "bad"})
        self.assertTrue(updated)
        query, values = connection.execute.call_args.args
        self.assertIn("title = %s", query)
        self.assertNotIn("not_a_column", query)
        self.assertEqual(values, ["Новое", 7])

    def test_selected_books_keep_requested_order(self):
        columns = [MagicMock(name=name) for name in ("id", "box", "author", "title", "publication_year", "publisher", "print_run", "language", "isbn", "genre")]
        for column, name in zip(columns, ("id", "box", "author", "title", "publication_year", "publisher", "print_run", "language", "isbn", "genre")):
            column.name = name
        cursor = MagicMock()
        cursor.description = columns
        cursor.fetchall.return_value = [(2, "b", "B", "Two", None, None, None, None, None, None), (1, "a", "A", "One", None, None, None, None, None, None)]
        connection = MagicMock(); connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        psycopg = types.SimpleNamespace(connect=MagicMock(return_value=connection), OperationalError=Exception)
        with patch("database.importlib.import_module", return_value=psycopg):
            books = get_books("postgresql://test", [1, 2])
        self.assertEqual([book["id"] for book in books], [1, 2])
