from typing import Any, Optional

from fastapi import HTTPException
try:  # pragma: no cover - optional import for supabase error handling
    from postgrest.exceptions import APIError
except Exception:  # pragma: no cover - fallback if dependency changes
    APIError = Exception  # type: ignore

from ..config import REQUIRE_OWNER
from .supabase_client import get_supabase


def require_supabase():
    sb = get_supabase()
    if sb is None:
        raise HTTPException(status_code=503, detail="Supabase client unavailable")
    return sb


def owner_matches(row: dict, caller_id: Optional[str]) -> bool:
    if not caller_id or not row:
        return False
    return str(caller_id) in {str(row.get("owner_id")), str(row.get("user_id"))}


def get_assignment(assignment_id: str, caller_id: Optional[str], columns: str = "*") -> dict:
    sb = require_supabase()
    try:
        resp = sb.table("assignments").select(columns).eq("id", assignment_id).maybe_single().execute()
        row = resp.data
    except APIError as exc:
        err = getattr(exc, "message", None) or getattr(exc, "args", [""])[0]
        if isinstance(err, dict) and str(err.get("code")) == "204":
            row = None
        else:
            raise
    if not row:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if REQUIRE_OWNER and caller_id and not owner_matches(row, caller_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    return row


def get_upload(upload_id: str, caller_id: Optional[str], columns: str = "*") -> dict:
    sb = require_supabase()
    try:
        resp = sb.table("uploads").select(columns).eq("id", upload_id).maybe_single().execute()
        row = resp.data
    except APIError as exc:
        err = getattr(exc, "message", None) or getattr(exc, "args", [""])[0]
        if isinstance(err, dict) and str(err.get("code")) == "204":
            row = None
        else:
            raise
    if not row:
        raise HTTPException(status_code=404, detail="Upload not found")
    if REQUIRE_OWNER and caller_id and not owner_matches(row, caller_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    return row


def update_upload(upload_id: str, payload: dict[str, Any]) -> None:
    sb = require_supabase()
    sb.table("uploads").update(payload).eq("id", upload_id).execute()
