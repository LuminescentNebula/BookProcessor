"""Production WSGI application; background work runs in worker.py."""

from app import create_app

app = create_app({"BOOTSTRAP_ADMIN": True})
