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

    def execute(self):
        if self.name == "uploads":
            uid = self._where.get("id")
            return _Resp(self.db["uploads"].get(uid))
        return _Resp(None)


class FakeStorage:
    def from_(self, name):
        class _B:
            def upload(self, key, data):
                return {"ok": True}
        return _B()


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


def test_grade_returns_scores():
    rows = {
        "u6": {
            "id": "u6",
            "owner_id": "owner-1",
            "storage_path": "submissions/owner-1/a.png",
            "status": "pending",
            "extracted_text": "2+2=4\nQ: add two numbers",
        }
    }
    app_mod = _fresh_app_with_supabase(rows)
    client = TestClient(app_mod.app)

    r = client.post("/api/grade/start", json={"upload_id": "u6"}, headers=_auth_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert isinstance(data["total_score"], (int, float))
    assert isinstance(data["items"], list)
