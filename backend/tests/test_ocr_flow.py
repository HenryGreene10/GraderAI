import json
import importlib
import types
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


class _Resp:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self._sel = None
        self._where = {}
        self._payload = None

    # query shape: select("*").eq("id", value).maybe_single().execute()
    def select(self, sel):
        self._sel = sel
        return self

    def eq(self, col, val):
        self._where[col] = val
        return self

    def maybe_single(self):
        return self

    def execute(self):
        if self.name == "uploads":
            uid = self._where.get("id")
            return _Resp(self.db["uploads"].get(uid))
        return _Resp(None)

    # update(payload).eq("id", value).execute()
    def update(self, payload):
        self._payload = payload
        return self


class FakeBucket:
    def __init__(self, parent, name):
        self.parent = parent
        self.name = name

    def create_signed_url(self, key, expires_in):
        # match supabase-py response shape
        return {"signedURL": f"https://signed.example/{self.name}/{key}?t=abc"}

    def upload(self, key, data):
        self.parent._uploads.append((self.name, key, data))


class FakeStorage:
    def __init__(self):
        self._uploads = []  # list of (bucket, key, data)

    def from_(self, name):
        return FakeBucket(self, name)


class FakeSupabase:
    def __init__(self, db_rows):
        # db_rows: { 'uploads': { id: rowdict } }
        self._db = db_rows
        self.storage = FakeStorage()

    def table(self, name):
        return FakeTable(self._db, name)


@pytest.fixture()
def fake_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")
    monkeypatch.setenv("HANDWRITINGOCR_API_KEY", "dummy")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:5173")
    # Critical: use mock to avoid real OCR/network
    monkeypatch.setenv("OCR_MOCK", "1")
    monkeypatch.setenv("OCR_PROVIDER", "hf")
    # Keep bucket simple/stable for tests
    monkeypatch.setenv("SUPABASE_BUCKET", "submissions")


def _fresh_app_with_supabase(fake_rows):
    # Import app module fresh so env is read
    import backend.app as app_mod
    importlib.reload(app_mod)
    # Patch supabase client with fake
    app_mod.supabase = FakeSupabase({"uploads": fake_rows})
    return app_mod


def _auth_headers(user_id="owner-1"):
    return {"X-Owner-Id": user_id, "X-User-Id": user_id}


def test_ocr_start_marks_done_and_sets_text(fake_env):
    rows = {
        "u1": {
            "id": "u1",
            "owner_id": "owner-1",
            "storage_path": "submissions/owner-1/sample.png",
            "status": "pending",
        }
    }
    app_mod = _fresh_app_with_supabase(rows)
    client = TestClient(app_mod.app)

    r = client.post("/api/ocr/start", json={"upload_id": "u1"}, headers=_auth_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["status"] == "done"

    # Verify row mutated
    row = rows["u1"]
    assert row["status"] == "OCR_DONE"
    assert "extracted_text" in row and "MOCK OCR" in row["extracted_text"]
    # timestamps set
    assert "ocr_started_at" in row and "ocr_completed_at" in row


def test_grade_stores_overlay_and_pdf(fake_env):
    rows = {
        "u2": {
            "id": "u2",
            "owner_id": "owner-1",
            "storage_path": "submissions/owner-1/another.png",
            "status": "pending",
            # Leave extracted_text empty to force inline OCR during grade
        }
    }
    app_mod = _fresh_app_with_supabase(rows)
    client = TestClient(app_mod.app)

    r = client.post("/api/grade", json={"upload_id": "u2"}, headers=_auth_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["upload_id"] == "u2"
    assert data["overlay_path"].endswith("u2.overlay.json")
    assert data["graded_pdf_path"].endswith("u2.pdf")

    # Storage uploads recorded
    uploads = app_mod.supabase.storage._uploads
    # Expect two uploads: overlay json and pdf bytes
    assert any(b == "graded-pdfs" and k.endswith("u2.overlay.json") for (b, k, _) in uploads)
    assert any(b == "graded-pdfs" and k.endswith("u2.pdf") for (b, k, _) in uploads)
