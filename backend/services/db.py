from typing import Any, Optional
import logging
import re

from fastapi import HTTPException
try:  # pragma: no cover - optional import for supabase error handling
    from postgrest.exceptions import APIError
except Exception:  # pragma: no cover - fallback if dependency changes
    APIError = Exception  # type: ignore

from ..config import REQUIRE_OWNER
from .supabase_client import get_supabase

logger = logging.getLogger(__name__)


def _error_dict(exc: Exception) -> Optional[dict]:
    raw = getattr(exc, "message", None)
    if isinstance(raw, dict):
        return raw
    args = getattr(exc, "args", None)
    if isinstance(args, (list, tuple)) and args:
        if isinstance(args[0], dict):
            return args[0]
    return None


def _missing_column(err: dict) -> Optional[str]:
    details = str(err.get("details") or err.get("message") or "")
    match = re.search(r"Could not find the '([^']+)' column", details)
    if not match:
        return None
    return match.group(1)


def _strip_column(columns: str, missing: str) -> str:
    parts = [c.strip() for c in (columns or "").split(",") if c.strip()]
    filtered = [c for c in parts if c != missing]
    return ",".join(filtered)


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
    row = None
    cols = columns
    for _ in range(3):
        try:
            resp = sb.table("assignments").select(cols).eq("id", assignment_id).maybe_single().execute()
            row = resp.data
            break
        except APIError as exc:
            err = _error_dict(exc)
            if isinstance(err, dict):
                code = str(err.get("code") or "")
                if code == "204":
                    row = None
                    break
                if code == "PGRST204":
                    missing = _missing_column(err)
                    if missing:
                        cols = _strip_column(cols, missing)
                        continue
            raise
    if not row:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if REQUIRE_OWNER and caller_id and not owner_matches(row, caller_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    return row


def get_upload(upload_id: str, caller_id: Optional[str], columns: str = "*") -> dict:
    sb = require_supabase()
    row = None
    cols = columns
    for _ in range(3):
        try:
            resp = sb.table("uploads").select(cols).eq("id", upload_id).maybe_single().execute()
            row = resp.data
            break
        except APIError as exc:
            err = _error_dict(exc)
            if isinstance(err, dict):
                code = str(err.get("code") or "")
                if code == "204":
                    row = None
                    break
                if code == "PGRST204":
                    missing = _missing_column(err)
                    if missing:
                        cols = _strip_column(cols, missing)
                        continue
            raise
    if not row:
        raise HTTPException(status_code=404, detail="Upload not found")
    if REQUIRE_OWNER and caller_id and not owner_matches(row, caller_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    return row


def update_upload(upload_id: str, payload: dict[str, Any]) -> None:
    sb = require_supabase()
    data = dict(payload)
    for _ in range(3):
        if not data:
            return
        try:
            sb.table("uploads").update(data).eq("id", upload_id).execute()
            return
        except APIError as exc:
            err = _error_dict(exc)
            if isinstance(err, dict) and str(err.get("code")) == "PGRST204":
                missing = _missing_column(err)
                if missing and missing in data:
                    logger.warning("Dropping missing uploads column '%s' from update", missing)
                    data.pop(missing, None)
                    continue
            raise
