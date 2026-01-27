from __future__ import annotations

from typing import Optional


def is_pdf_bytes(blob: bytes) -> bool:
    return bool(blob) and blob[:4] == b"%PDF"


def is_pdf_mime(mime_type: Optional[str]) -> bool:
    return (mime_type or "").lower().endswith("pdf")


def is_image_mime(mime_type: Optional[str]) -> bool:
    return (mime_type or "").lower().startswith("image/")
