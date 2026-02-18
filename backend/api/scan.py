from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

router = APIRouter(prefix="/api/scan", tags=["scan"])

_DEPRECATED_DETAIL = (
    "scan_workflow_deprecated: Scanner/QR upload routes are disabled for beta. "
    "Use assignment upload endpoints and upload scanned PDFs."
)


def _raise_deprecated() -> None:
    raise HTTPException(status_code=410, detail=_DEPRECATED_DETAIL)


@router.get("/{token}/status")
def scan_status(token: str):
    _ = token
    _raise_deprecated()


@router.post("/{token}/upload")
async def scan_upload(
    token: str,
    file: UploadFile = File(...),
    page_count: int | None = Form(None),
    page_sizes: str | None = Form(None),
):
    _ = (token, file, page_count, page_sizes)
    _raise_deprecated()
