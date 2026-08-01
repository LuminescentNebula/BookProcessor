import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app import create_app
from database import DatabaseUnavailable
from worker import FolderWorker, run_job, worker_is_healthy


class WorkerTests(unittest.TestCase):
    def test_container_uses_gunicorn_and_separate_worker_service(self):
        dockerfile = Path("Dockerfile").read_text()
        compose = Path("compose.yaml").read_text()
        self.assertIn("gunicorn", dockerfile)
        self.assertIn("web_app:app", dockerfile)
        self.assertIn("  worker:", compose)
        self.assertIn('["CMD", "python", "worker.py", "--health"]', compose)

    def make_app(self):
        database = MagicMock()
        services = SimpleNamespace(
            database=database,
            process_books=MagicMock(return_value=[{"Название": "Книга"}]),
            normalize_metadata=MagicMock(),
            internet_search=MagicMock(),
        )
        return create_app({"TESTING": True, "SERVICES": services, "BOOTSTRAP_ADMIN": False}), services

    def test_sigterm_stop_flag_prevents_new_work(self):
        app, _ = self.make_app()
        worker = FolderWorker(app)
        worker.stop()
        self.assertTrue(worker.stop_event.is_set())

    def test_current_job_is_released_when_database_is_down_during_shutdown(self):
        app, services = self.make_app()
        services.database.save_books.side_effect = DatabaseUnavailable("offline")
        state = app.extensions["job_state"]
        key = ("/photos/box", "signature")
        state.queued_sources.add(key)
        state.jobs["job"] = {"status": "queued", "completed": 0, "total": 0,
            "rows": [], "error": None, "database_job_id": None,
            "started_at": 0, "finished_at": None}
        stopped = threading.Event(); stopped.set()
        self.assertFalse(run_job(app, "job", Path("/photos"), ["box"], ["model"], 1,
                                 "http://ollama", 30, key[0], key[1], stopped))
        self.assertNotIn(key, state.queued_sources)
        self.assertIn("возвращено в очередь", state.jobs["job"]["error"])

    def test_worker_health_uses_fresh_heartbeat(self):
        app, _ = self.make_app()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker.json"
            worker = FolderWorker(app, heartbeat_path=path)
            worker.heartbeat("idle")
            self.assertTrue(worker_is_healthy(path, max_age=10))
            data = json.loads(path.read_text())
            data["status"] = "stopped"
            path.write_text(json.dumps(data))
            self.assertFalse(worker_is_healthy(path, max_age=10))


if __name__ == "__main__":
    unittest.main()
