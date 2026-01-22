from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from ..config import SUBMISSIONS_BUCKET
from ..services.db import get_upload
from ..services.storage import strip_bucket_prefix
from ..services.supabase_client import get_supabase

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.delete("/{upload_id}")
def delete_upload(
    upload_id: str,
    x_owner_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
):
    caller_id = x_owner_id or x_user_id
    row = get_upload(upload_id, caller_id, columns="id,owner_id,storage_path")
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
