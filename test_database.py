import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from database import DatabaseUnavailable, INSERT_BOOK, save_books, source_was_processed


class DatabaseTests(unittest.TestCase):
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
