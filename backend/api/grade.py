import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import get_current_user_id
from ..services.db import get_upload
from .uploads import run_unified_submission_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(tags=["grade"])


class StartGradeBody(BaseModel):
    upload_id: str


@router.post("/api/grade")
async def start_grade(
    body: StartGradeBody,
    user_id: str = Depends(get_current_user_id),
):
    """
    Legacy compatibility endpoint.

    Canonical behavior is delegated to the unified uploads pipeline so all
    entry points share normalization, template grading, and overlay rendering.
    """
    result = await run_unified_submission_pipeline(
        body.upload_id,
        user_id,
        source="legacy.api_grade",
    )

    row = get_upload(
        body.upload_id,
        user_id,
        columns="id,owner_id,grade_json,overlay_path,graded_pdf_path,needs_review",
    )

    return {
        "ok": True,
        "upload_id": row["id"],
        "needs_review": bool(row.get("needs_review")),
        "graded_pdf_path": row.get("graded_pdf_path"),
        "overlay_path": row.get("overlay_path"),
        "grade": row.get("grade_json") or {},
        "pipeline_source": result.get("pipeline_source") or "legacy.api_grade",
    }
