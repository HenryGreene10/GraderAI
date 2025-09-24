import importlib
import httpx
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

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def execute(self):
        if self.name == "uploads":
            uid = self._where.get("id")
            row = self.db["uploads"].get(uid)
            if self._op == "update" and row:
                row.update(self._payload)
            return _Resp(row)
        if self.name == "ocr_results":
            return _Resp(None)
        return _Resp(None)


class FakeBucket:
    def __init__(self, parent, name):
        self.parent = parent
        self.name = name

    def create_signed_url(self, key, expires_in):
        return {"signedURL": f"https://signed.example/{self.name}/{key}?t=abc"}


class FakeStorage:
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


def test_ocr_start_happy_path(monkeypatch):
    monkeypatch.setenv("OCR_MOCK", "1")
    rows = {
        "u3": {"id": "u3", "owner_id": "owner-1", "storage_path": "submissions/owner-1/z.png", "status": "pending"}
    }
    app_mod = _fresh_app_with_supabase(rows)
    client = TestClient(app_mod.app)

    r = client.post("/api/ocr/start", json={"upload_id": "u3"}, headers=_auth_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["status"] == "done"
    # DB row now shows terminal status
    assert rows["u3"]["status"] == "OCR_DONE"


def test_ocr_timeout_returns_500(monkeypatch):
    # Force real provider path and simulate timeout
    monkeypatch.setenv("OCR_MOCK", "0")
    rows = {
        "u4": {"id": "u4", "owner_id": "owner-1", "storage_path": "submissions/owner-1/t.png", "status": "pending"}
    }
    app_mod = _fresh_app_with_supabase(rows)

    async def raise_timeout(*args, **kwargs):
        raise httpx.ReadTimeout("timeout", request=httpx.Request("POST", "https://example"))

    # Patch adapter call site
    monkeypatch.setattr(app_mod.ocr, "extract_text", raise_timeout)

    client = TestClient(app_mod.app)
    r = client.post("/api/ocr/start", json={"upload_id": "u4"}, headers=_auth_headers())
    assert r.status_code == 500
    # Row marked with error
    assert rows["u4"]["status"] == "OCR_ERROR"
