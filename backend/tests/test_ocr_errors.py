import json
import importlib
from typing import Any

import pytest
import httpx
from fastapi.testclient import TestClient
from backend.services import ocr


class _Resp:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self._where = {}
        self._payload = None

    def select(self, sel):
        self._sel = sel
        return self

    def eq(self, col, val):
        self._where[col] = val
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def insert(self, payload: dict):
        self._payload = payload
        self._op = "insert"
        return self

    def update(self, payload: dict):
        self._payload = payload
        self._op = "update"
        return self

    def execute(self):
        if self.name == "uploads":
            if getattr(self, "_op", None) == "update":
                uid = self._where.get("id")
                row = self.db["uploads"].get(uid)
                if row:
                    row.update(self._payload)
                return _Resp(row)
            # select path
            uid = self._where.get("id")
            return _Resp(self.db["uploads"].get(uid))
        if self.name == "ocr_results":
            if getattr(self, "_op", None) == "insert":
                rec = dict(self._payload)
                self.db.setdefault("ocr_results", []).append(rec)
                return _Resp(rec)
        return _Resp(None)


class FakeBucket:
    def __init__(self, parent, name):
        self.parent = parent
        self.name = name

    def create_signed_url(self, key, expires_in):
        return {"signedURL": f"https://signed.example/{self.name}/{key}?t=abc"}

    def upload(self, key, data):
        self.parent._uploads.append((self.name, key, data))


class FakeStorage:
    def __init__(self):
        self._uploads = []

    def from_(self, name):
        return FakeBucket(self, name)


class FakeSupabase:
    def __init__(self, db_rows):
        self._db = db_rows
        self.storage = FakeStorage()

    def table(self, name):
        return FakeTable(self._db, name)


@pytest.fixture()
def fake_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:5173")
    # Hugging Face provider enabled
    monkeypatch.setenv("OCR_MOCK", "0")
    monkeypatch.setenv("OCR_PROVIDER", "hf")
    monkeypatch.setenv("HF_API_URL", "https://api-inference.huggingface.co/models/test-model")
    monkeypatch.setenv("HF_TOKEN", "fake-token")


def _fresh_app_with_supabase(fake_rows):
    import backend.app as app_mod
    importlib.reload(app_mod)
    app_mod.supabase = FakeSupabase({"uploads": fake_rows, "ocr_results": []})
    return app_mod


def _auth_headers(user_id="owner-1"):
    return {"X-Owner-Id": user_id, "X-User-Id": user_id}


@pytest.mark.parametrize("status_code_sequence", [
    [500, 200],
])
def test_ocr_retry_then_success(fake_env, monkeypatch, status_code_sequence):
    # Arrange upload row
    rows = {
        "u-r1": {"id": "u-r1", "owner_id": "owner-1", "storage_path": "submissions/owner-1/x.png", "status": "pending"}
    }
    app_mod = _fresh_app_with_supabase(rows)

    # Force real provider path (not mock) and bypass network download
    monkeypatch.setenv("OCR_MOCK", "0")
    async def fake_download_bytes(url: str) -> bytes:
        return b"bytes"
    monkeypatch.setattr(app_mod, "_download_bytes", fake_download_bytes)

    # Mock HF httpx client inside services.ocr to return 500 then 200
    seq = list(status_code_sequence)

    class FakeResponse:
        def __init__(self, status_code: int, obj: Any = None):
            self.status_code = status_code
            self._obj = obj if obj is not None else {"text": "ok"}
            self.text = json.dumps(self._obj)

        def raise_for_status(self):
            if self.status_code >= 400:
                request = httpx.Request("POST", "https://api-inference.huggingface.co/models/test-model")
                raise httpx.HTTPStatusError("error", request=request, response=httpx.Response(self.status_code, request=request, text=self.text))

        def json(self):
            return self._obj

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, *args, **kwargs):
            code = seq.pop(0)
            return FakeResponse(code)

    monkeypatch.setattr(ocr, "httpx", type("HX", (), {"AsyncClient": lambda timeout=60: FakeClient()}))

    client = TestClient(app_mod.app)
    r = client.post("/api/ocr/start", json={"upload_id": "u-r1"}, headers=_auth_headers())
    assert r.status_code == 200, r.text

    # Upload status should end OCR_DONE
    assert rows["u-r1"]["status"] == "OCR_DONE"

    # Check attempts log recorded 500 then 200
    attempts = app_mod.supabase._db["ocr_results"]
    assert attempts, "ocr_results should have entries"
    last = attempts[-1]
    logged = json.loads(last.get("attempts_log") or "[]")
    assert 500 in logged and 200 in logged


def test_ocr_timeout_error(fake_env, monkeypatch):
    # Arrange upload row
    rows = {
        "u-e1": {"id": "u-e1", "owner_id": "owner-1", "storage_path": "submissions/owner-1/y.png", "status": "pending"}
    }
    app_mod = _fresh_app_with_supabase(rows)

    # Force real provider path (not mock) and bypass network download
    monkeypatch.setenv("OCR_MOCK", "0")
    async def fake_download_bytes(url: str) -> bytes:
        return b"bytes"
    monkeypatch.setattr(app_mod, "_download_bytes", fake_download_bytes)

    # Mock HF httpx client inside services.ocr to raise ReadTimeout
    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("timeout", request=httpx.Request("POST", "https://api-inference.huggingface.co/models/test-model"))

    monkeypatch.setattr(ocr, "httpx", type("HX", (), {"AsyncClient": lambda timeout=60: FakeClient()}))

    client = TestClient(app_mod.app)
    r = client.post("/api/ocr/start", json={"upload_id": "u-e1"}, headers=_auth_headers())
    assert r.status_code in (500, 502)

    # Upload should be marked OCR_ERROR
    assert rows["u-e1"]["status"] == "OCR_ERROR"

    # attempts_log should contain 'timeout'
    attempts = app_mod.supabase._db["ocr_results"]
    assert attempts, "ocr_results should have entries"
    last = attempts[-1]
    logged = json.loads(last.get("attempts_log") or "[]")
    assert any(str(x).lower().find("timeout") >= 0 for x in logged)
