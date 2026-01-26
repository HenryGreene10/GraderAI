import json
from typing import Any

from ..config import SUBMISSIONS_BUCKET
from .supabase_client import get_supabase


def strip_bucket_prefix(path: str, bucket: str) -> str:
    p = (path or "").lstrip("/").replace("\\", "/")
    if p.startswith(f"{bucket}/"):
        return p[len(bucket) + 1 :]
    return p


def download_submission_bytes(storage_path: str) -> bytes:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase client unavailable")
    rel = strip_bucket_prefix(storage_path, SUBMISSIONS_BUCKET)
    blob = sb.storage.from_(SUBMISSIONS_BUCKET).download(rel)
    if not blob:
        raise RuntimeError(f"Submission not found: {storage_path}")
    return blob


def upload_bytes(bucket: str, key: str, data: bytes, content_type: str) -> None:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase client unavailable")
    sb.storage.from_(bucket).upload(key, data, {"content-type": content_type, "upsert": "true"})


def upload_json(bucket: str, key: str, payload: Any) -> None:
    upload_bytes(bucket, key, json.dumps(payload).encode("utf-8"), "application/json")
