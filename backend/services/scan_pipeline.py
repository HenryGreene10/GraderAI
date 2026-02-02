from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..config import SUBMISSIONS_BUCKET
from .file_utils import is_pdf_bytes, is_pdf_mime, is_image_mime
from .scanner import normalize_image_bytes, ScanResult
from .storage import upload_bytes


@dataclass
class ScanArtifacts:
    normalized_image_path: str
    normalized_pdf_path: str
    width_px: int
    height_px: int
    scan_ok: bool
    error: Optional[str]
    normalized_image_bytes: bytes
    normalized_pdf_bytes: bytes


def build_scan_artifacts(image_bytes: bytes, owner_id: str, upload_id: str) -> ScanArtifacts:
    scan: ScanResult = normalize_image_bytes(image_bytes)
    image_key = f"{owner_id}/normalized/{upload_id}.png"
    pdf_key = f"{owner_id}/normalized/{upload_id}.pdf"

    upload_bytes(SUBMISSIONS_BUCKET, image_key, scan.normalized_png, "image/png")
    upload_bytes(SUBMISSIONS_BUCKET, pdf_key, scan.normalized_pdf, "application/pdf")

    return ScanArtifacts(
        normalized_image_path=f"{SUBMISSIONS_BUCKET}/{image_key}",
        normalized_pdf_path=f"{SUBMISSIONS_BUCKET}/{pdf_key}",
        width_px=scan.width_px,
        height_px=scan.height_px,
        scan_ok=scan.scan_ok,
        error=scan.error,
        normalized_image_bytes=scan.normalized_png,
        normalized_pdf_bytes=scan.normalized_pdf,
    )


def prepare_ocr_image(
    blob: bytes,
    mime_type: Optional[str],
    owner_id: str,
    upload_id: str,
) -> tuple[bytes, Optional[ScanArtifacts]]:
    if is_pdf_mime(mime_type) or is_pdf_bytes(blob):
        return blob, None
    if mime_type and not is_image_mime(mime_type):
        return blob, None
    artifacts = build_scan_artifacts(blob, owner_id, upload_id)
    return artifacts.normalized_image_bytes, artifacts
