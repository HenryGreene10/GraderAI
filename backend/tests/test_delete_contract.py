import importlib
from fastapi.testclient import TestClient


class _Resp:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self._where = {}
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
            if self._op == "delete" and uid in self.db["uploads"]:
                del self.db["uploads"][uid]
                return _Resp(None)
            return _Resp(self.db["uploads"].get(uid))
        return _Resp(None)


class FakeBucket:
    def __init__(self, parent, name):
        self.parent = parent
        self.name = name

    def remove(self, keys):
        self.parent.removed.extend(keys)
        return {"data": keys}


class FakeStorage:
    def __init__(self):
        self.removed = []

    def from_(self, name):
        return FakeBucket(self, name)


class FakeSupabase:
    def __init__(self, db_rows):
        self._db = db_rows
        self.storage = FakeStorage()

    def table(self, name):
        return FakeTable(self._db, name)


def _fresh_app_with_supabase(fake_rows):
    import backend.app as app_mod
    importlib.reload(app_mod)
    app_mod.supabase = FakeSupabase({"uploads": fake_rows})
    return app_mod


def _auth_headers(user_id="owner-1"):
    return {"X-Owner-Id": user_id, "X-User-Id": user_id}


def test_delete_bucket_relative_and_ok():
    rows = {
        "u5": {
            "id": "u5",
            "owner_id": "owner-1",
            "storage_path": "submissions/owner-1/assign/file3.png",
            "status": "pending",
        }
    }
    app_mod = _fresh_app_with_supabase(rows)
    client = TestClient(app_mod.app)

    r = client.delete("/api/uploads/u5", headers=_auth_headers())
    assert r.status_code == 200
    assert r.json().get("ok") is True
    # Bucket-relative key must be passed to remove
    assert app_mod.supabase.storage.removed[-1] == "owner-1/assign/file3.png"
