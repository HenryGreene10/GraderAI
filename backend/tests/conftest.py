import os
from typing import Any

import pytest

from backend.services.supabase_client import set_supabase

os.environ.setdefault("OCR_MOCK", "1")
os.environ.setdefault("OCR_PROVIDER", "mock")
os.environ.pop("HF_TOKEN", None)
os.environ.setdefault("CORS_ALLOW_ORIGINS", "http://localhost:5173")


class _Resp:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self._where = {}
        self._payload = None
        self._op = None

    def select(self, _sel):
        return self

    def eq(self, col, val):
        self._where[col] = val
        return self

    def maybe_single(self):
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def execute(self):
        if self.name == "uploads":
            uid = self._where.get("id")
            if self._op == "delete" and uid in self.db["uploads"]:
                del self.db["uploads"][uid]
                return _Resp(None)
            row = self.db["uploads"].get(uid)
            if self._op == "update" and row is not None:
                row.update(self._payload or {})
            return _Resp(row)
        if self.name == "overrides":
            if self._op == "insert":
                self.db["overrides"].append(self._payload or {})
            return _Resp(self._payload)
        return _Resp(None)


class FakeAuth:
    def __init__(self, token_map=None):
        self.token_map = token_map or {}

    def get_user(self, token):
        if not token:
            raise Exception("invalid token")
        user_id = self.token_map.get(token)
        if not user_id:
            if token.startswith("user:"):
                user_id = token.split("user:", 1)[1]
            else:
                user_id = token
        if not user_id:
            raise Exception("invalid token")
        return {"user": {"id": user_id}}


class FakeBucket:
    def __init__(self, storage, name):
        self.storage = storage
        self.name = name

    def download(self, key):
        return self.storage.objects.get((self.name, key))

    def upload(self, key, data, _opts=None):
        self.storage.objects[(self.name, key)] = data
        return {"ok": True}

    def remove(self, keys):
        for key in keys:
            self.storage.objects.pop((self.name, key), None)
            self.storage.removed.append((self.name, key))
        return {"ok": True}


class FakeStorage:
    def __init__(self):
        self.objects = {}
        self.removed = []

    def from_(self, name):
        return FakeBucket(self, name)


class FakeSupabase:
    def __init__(self, db):
        self._db = db
        self.storage = FakeStorage()
        self.auth = FakeAuth()

    def table(self, name):
        return FakeTable(self._db, name)


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    monkeypatch.setenv("OCR_MOCK", "1")
    monkeypatch.setenv("OCR_PROVIDER", "mock")
    monkeypatch.delenv("HF_TOKEN", raising=False)


@pytest.fixture()
def fake_supabase():
    db = {"uploads": {}, "overrides": []}
    client = FakeSupabase(db)
    set_supabase(client)
    try:
        yield client
    finally:
        set_supabase(None)
