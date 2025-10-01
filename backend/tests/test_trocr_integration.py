import io
import importlib
import json
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image


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

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        # Persist simple row for ocr_runs
        if self.name not in self.db:
            self.db[self.name] = []
        self.db[self.name].append(payload)
        return self

    def execute(self):
        if self.name == "uploads":
            uid = self._where.get("id")
            row = self.db["uploads"].get(uid)
            if self._op == "update" and row:
                row.update(self._payload)
            return _Resp(row)
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


def test_trocr_local_endpoint_success(monkeypatch):
    # Configure local provider
    monkeypatch.setenv("OCR_PROVIDER", "trocr_local")
    monkeypatch.setenv("OCR_MODEL", "microsoft/trocr-base-handwritten")
    monkeypatch.setenv("OCR_MODE", "single")
    # Construct small JPEG bytes
    img = Image.new("RGB", (2, 2), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    jpg_bytes = buf.getvalue()

    # Fake pipeline returning text
    tl = importlib.import_module("backend.ocr.providers.trocr_local")

    class FakePipe:
        def __call__(self, image):
            return [{"generated_text": "Hello"}]

    monkeypatch.setattr(tl, "pipeline", lambda *a, **k: FakePipe())

    rows = {
        "u10": {
            "id": "u10",
            "owner_id": "owner-1",
            "storage_path": "submissions/owner-1/sample.jpg",
            "status": "pending",
        }
    }
    app_mod = _fresh_app_with_supabase(rows)

    # Bypass network to fetch the image bytes
    async def fake_download_bytes(url: str) -> bytes:
        return jpg_bytes

    monkeypatch.setattr(app_mod, "_download_bytes", fake_download_bytes)

    client = TestClient(app_mod.app)
    r = client.post("/api/ocr/start", json={"upload_id": "u10"}, headers=_auth_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["status"] == "done"
    assert data["text_len"] >= 1
    # Upload row updated
    assert rows["u10"]["ocr_status"] == "done"
    assert isinstance(rows["u10"].get("ocr_text"), str) and rows["u10"]["ocr_text"].strip() != ""

