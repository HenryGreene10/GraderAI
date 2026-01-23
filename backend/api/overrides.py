import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user_id
from ..services.db import get_upload, update_upload
from ..services.supabase_client import get_supabase

router = APIRouter(prefix="/api", tags=["overrides"])


class OverrideBody(BaseModel):
    upload_id: str
    overrides: dict[str, Any]


def _utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@router.post("/override")
def apply_override(
    body: OverrideBody,
    user_id: str = Depends(get_current_user_id),
):
    row = get_upload(body.upload_id, user_id, columns="id,owner_id")

    sb = get_supabase()
    if sb is None:
        raise HTTPException(status_code=503, detail="Supabase client unavailable")

    try:
        sb.table("overrides").insert(
            {
                "upload_id": row["id"],
                "owner_id": row.get("owner_id") or user_id,
                "overrides_json": body.overrides,
                "created_at": _utc_iso(),
                "updated_at": _utc_iso(),
            }
        ).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"override_insert_failed: {exc}")

    update_upload(
        row["id"],
        {
            "status": "overridden",
            "updated_at": _utc_iso(),
        },
    )

    return {"ok": True, "upload_id": row["id"]}
