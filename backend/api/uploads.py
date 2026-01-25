from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_user_id
from ..config import SUBMISSIONS_BUCKET
from ..services.db import get_upload
from ..services.storage import strip_bucket_prefix
from ..services.supabase_client import get_supabase

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _extract_signed_url(result: object) -> Optional[str]:
    if isinstance(result, dict):
        return (
            result.get("signedURL")
            or result.get("signedUrl")
            or result.get("signed_url")
            or result.get("url")
        )
    getter = getattr(result, "get", None)
    if callable(getter):
        return (
            getter("signedURL")
            or getter("signedUrl")
            or getter("signed_url")
            or getter("url")
        )
    return None


@router.get("/{upload_id}/preview")
def preview_upload(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
):
    row = get_upload(upload_id, user_id, columns="id,owner_id,storage_path")
    storage_path = row.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=404, detail="Missing storage_path")

    sb = get_supabase()
    if sb is None:
        raise HTTPException(status_code=503, detail="Supabase client unavailable")

    rel = strip_bucket_prefix(storage_path, SUBMISSIONS_BUCKET)
    try:
        result = sb.storage.from_(SUBMISSIONS_BUCKET).create_signed_url(rel, 300)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"signed_url_failed: {exc}")

    url = _extract_signed_url(result)
    if not url:
        raise HTTPException(status_code=500, detail="signed_url_missing")

    return {"url": url}


@router.delete("/{upload_id}")
def delete_upload(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
):
    row = get_upload(upload_id, user_id, columns="id,owner_id,storage_path")
    storage_path = row.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=404, detail="Missing storage_path")

    sb = get_supabase()
    if sb is None:
        raise HTTPException(status_code=503, detail="Supabase client unavailable")

    try:
        rel = strip_bucket_prefix(storage_path, SUBMISSIONS_BUCKET)
        sb.storage.from_(SUBMISSIONS_BUCKET).remove([rel])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"storage_delete_failed: {exc}")

    try:
        sb.table("uploads").delete().eq("id", row["id"]).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"db_delete_failed: {exc}")

    return {"ok": True, "upload_id": row["id"]}
