import importlib
import pytest
from fastapi.testclient import TestClient


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

    def select(self, sel):
        self._sel = sel
        return self

    def eq(self, col, val):
        self._where[col] = val
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        if self.name == "uploads":
            uid = self._where.get("id")
            if self._op == "delete":
                if uid in self.db["uploads"]:
                    del self.db["uploads"][uid]
                return _Resp(None)
            # select path
            return _Resp(self.db["uploads"].get(uid))
        return _Resp(None)


class FakeBucket:
    def __init__(self, parent, name):
        self.parent = parent
        self.name = name

    def remove(self, keys):
        # delegate to parent behavior
        return self.parent._remove(keys)


class FakeStorage:
    def __init__(self, behavior="ok"):
        self.behavior = behavior
        self.removed = []

    def _remove(self, keys):
        if self.behavior == "fail":
            raise RuntimeError("boom")
        self.removed.extend(keys)
        return {"data": keys}

    def from_(self, name):
        return FakeBucket(self, name)


class FakeSupabase:
    def __init__(self, db_rows, storage_behavior="ok"):
        self._db = db_rows
        self.storage = FakeStorage(storage_behavior)

    def table(self, name):
        return FakeTable(self._db, name)


@pytest.fixture()
def fake_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("SUPABASE_BUCKET", "submissions")


def _fresh_app_with_supabase(fake_rows, storage_behavior="ok"):
    import backend.app as app_mod
    importlib.reload(app_mod)
    app_mod.supabase = FakeSupabase({"uploads": fake_rows}, storage_behavior)
    return app_mod


def _auth_headers(user_id="owner-1"):
    return {"X-Owner-Id": user_id, "X-User-Id": user_id}


def test_delete_success(fake_env):
    rows = {
        "u-del1": {
            "id": "u-del1",
            "owner_id": "owner-1",
            "storage_path": "submissions/owner-1/assign/file1.png",
            "status": "pending",
        }
    }
    app_mod = _fresh_app_with_supabase(rows, storage_behavior="ok")
    client = TestClient(app_mod.app)

    r = client.delete("/api/uploads/u-del1", headers=_auth_headers())
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True
    # DB row deleted
    assert "u-del1" not in rows
    # Storage removed received bucket-relative key
    removed = app_mod.supabase.storage.removed
    assert removed and removed[-1] == "owner-1/assign/file1.png"


def test_delete_storage_failure(fake_env):
    rows = {
        "u-del2": {
            "id": "u-del2",
            "owner_id": "owner-1",
            "storage_path": "submissions/owner-1/assign/file2.png",
            "status": "pending",
        }
    }
    app_mod = _fresh_app_with_supabase(rows, storage_behavior="fail")
    client = TestClient(app_mod.app)

    r = client.delete("/api/uploads/u-del2", headers=_auth_headers())
    assert r.status_code == 502
    # DB row must remain
    assert "u-del2" in rows

