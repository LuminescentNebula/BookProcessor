import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from database import INSERT_BOOK, save_books


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
            "Название файла фото обложки": "1.jpg", "Название файла фото информации": "2.jpg",
        }
        with patch.dict(sys.modules, {"psycopg": psycopg}):
            job_id = save_books("postgresql://test", [row], "/photos", ["box-1"], ["vision"])
        self.assertEqual(job_id, 42)
        psycopg.connect.assert_called_once_with("postgresql://test")
        cursor.executemany.assert_called_once()
        self.assertEqual(cursor.executemany.call_args.args[0], INSERT_BOOK)
        self.assertEqual(cursor.executemany.call_args.args[1][0][1:4], ("box-1", "Автор", "Книга"))
