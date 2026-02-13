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
            if self._op == "insert":
                payload = self._payload or {}
                if isinstance(payload, list):
                    for row in payload:
                        self.db["uploads"][row["id"]] = row
                    return _Resp(payload)
                self.db["uploads"][payload["id"]] = payload
                return _Resp(payload)

            if self._op == "delete":
                uid = self._where.get("id")
                if uid and uid in self.db["uploads"]:
                    del self.db["uploads"][uid]
                    return _Resp(None)
                assignment_id = self._where.get("assignment_id")
                owner_id = self._where.get("owner_id")
                if assignment_id:
                    to_delete = [
                        key
                        for key, row in self.db["uploads"].items()
                        if row.get("assignment_id") == assignment_id
                        and (not owner_id or row.get("owner_id") == owner_id)
                    ]
                    for key in to_delete:
                        del self.db["uploads"][key]
                    return _Resp(None)

            uid = self._where.get("id")
            if uid:
                row = self.db["uploads"].get(uid)
                if self._op == "update" and row is not None:
                    row.update(self._payload or {})
                return _Resp(row)

            rows = []
            for row in self.db["uploads"].values():
                match = True
                for key, value in self._where.items():
                    if row.get(key) != value:
                        match = False
                        break
                if match:
                    rows.append(row)
            return _Resp(rows)
        if self.name == "overrides":
            if self._op == "insert":
                self.db["overrides"].append(self._payload or {})
            return _Resp(self._payload)
        if self.name == "assignments":
            if self._op == "insert":
                payload = self._payload or {}
                if isinstance(payload, list):
                    for row in payload:
                        self.db["assignments"][row["id"]] = row
                    return _Resp(payload)
                self.db["assignments"][payload["id"]] = payload
                return _Resp(payload)
            if self._op == "delete":
                aid = self._where.get("id")
                if aid and aid in self.db["assignments"]:
                    del self.db["assignments"][aid]
                return _Resp(None)

            aid = self._where.get("id")
            if aid:
                row = self.db["assignments"].get(aid)
                if self._op == "update" and row is not None:
                    row.update(self._payload or {})
                return _Resp(row)

            rows = []
            for row in self.db["assignments"].values():
                match = True
                for key, value in self._where.items():
                    if row.get(key) != value:
                        match = False
                        break
                if match:
                    rows.append(row)
            return _Resp(rows)
        if self.name == "scan_sessions":
            if self._op == "insert":
                payload = self._payload or {}
                if isinstance(payload, list):
                    for row in payload:
                        self.db["scan_sessions"][row["id"]] = row
                    return _Resp(payload)
                self.db["scan_sessions"][payload["id"]] = payload
                return _Resp(payload)
            if self._op == "update":
                updated = None
                for row in self.db["scan_sessions"].values():
                    match = True
                    for key, value in self._where.items():
                        if row.get(key) != value:
                            match = False
                            break
                    if match:
                        row.update(self._payload or {})
                        updated = row
                return _Resp(updated)

            sid = self._where.get("id")
            if sid:
                return _Resp(self.db["scan_sessions"].get(sid))
            token = self._where.get("token")
            if token:
                for row in self.db["scan_sessions"].values():
                    if row.get("token") == token:
                        return _Resp(row)

            rows = []
            for row in self.db["scan_sessions"].values():
                match = True
                for key, value in self._where.items():
                    if row.get(key) != value:
                        match = False
                        break
                if match:
                    rows.append(row)
            return _Resp(rows)
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
    db = {"uploads": {}, "overrides": [], "assignments": {}, "scan_sessions": {}}
    client = FakeSupabase(db)
    set_supabase(client)
    try:
        yield client
    finally:
        set_supabase(None)
