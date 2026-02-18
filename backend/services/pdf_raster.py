from __future__ import annotations

from io import BytesIO
from typing import Any, Literal

from PIL import Image, ImageOps
from pypdf import PdfReader


def pdf_page_sizes_px(pdf_bytes: bytes, *, dpi: float) -> list[dict[str, int]]:
    reader = PdfReader(BytesIO(pdf_bytes))
    out: list[dict[str, int]] = []
    for page in reader.pages:
        box = page.mediabox
        try:
            width_pt = float(box.width)
            height_pt = float(box.height)
        except Exception:
            continue
        width_px = max(1, int(round((width_pt / 72.0) * float(dpi))))
        height_px = max(1, int(round((height_pt / 72.0) * float(dpi))))
        out.append({"width_px": width_px, "height_px": height_px})
    return out


def _page_rotation_degrees(page: Any) -> int:
    try:
        raw = int(page.get("/Rotate") or 0)
    except Exception:
        raw = 0
    raw = raw % 360
    if raw in {90, 180, 270}:
        return raw
    return 0


def _target_page_size_px(page: Any, *, dpi: float, rotation_deg: int) -> tuple[int, int]:
    box = page.mediabox
    width_pt = float(box.width)
    height_pt = float(box.height)
    width_px = max(1, int(round((width_pt / 72.0) * float(dpi))))
    height_px = max(1, int(round((height_pt / 72.0) * float(dpi))))
    if rotation_deg in {90, 270}:
        return height_px, width_px
    return width_px, height_px


def _to_png_bytes(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _rotate_for_page(image: Image.Image, rotation_deg: int) -> Image.Image:
    if rotation_deg in {90, 180, 270}:
        # PDF rotation is clockwise; PIL rotate positive is counter-clockwise.
        return image.rotate(-rotation_deg, expand=True)
    return image


def _decode_embedded_image_png(data: bytes, rotation_deg: int) -> tuple[bytes, int]:
    with Image.open(BytesIO(data)) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
        image = _rotate_for_page(image, rotation_deg)
        area = int(image.width * image.height)
        return _to_png_bytes(image), area


def _render_page_with_pillow(
    payload: bytes,
    *,
    page_index: int,
    target_size: tuple[int, int],
    rotation_deg: int,
) -> bytes | None:
    try:
        with Image.open(BytesIO(payload)) as doc:
            frame_count = int(getattr(doc, "n_frames", 1) or 1)
            if page_index < 0 or page_index >= frame_count:
                return None
            doc.seek(page_index)
            image = ImageOps.exif_transpose(doc).convert("RGB")
            image = _rotate_for_page(image, rotation_deg)
            if image.size != target_size:
                image = image.resize(target_size, Image.BICUBIC)
            return _to_png_bytes(image)
    except Exception:
        return None


def extract_pdf_page_raster_png(
    payload: bytes,
    *,
    page_index: int = 0,
    dpi: float = 300.0,
) -> tuple[bytes, Literal["largest_embedded_raster", "rendered_page", "blank_page_fallback"]]:
    reader = PdfReader(BytesIO(payload))
    if not reader.pages:
        raise ValueError("pdf_empty")
    if page_index < 0 or page_index >= len(reader.pages):
        raise ValueError("pdf_page_index_out_of_range")
    page = reader.pages[page_index]
    rotation_deg = _page_rotation_degrees(page)

    # Deterministic master-key/student-frame selection: largest pixel-area raster on page.
    best_png: bytes | None = None
    best_key: tuple[int, int, int] | None = None
    for idx, image_obj in enumerate(list(page.images or [])):
        data = bytes(getattr(image_obj, "data", b""))
        if not data:
            continue
        try:
            candidate_png, area = _decode_embedded_image_png(data, rotation_deg)
        except Exception:
            continue
        key = (area, len(data), -idx)
        if best_key is None or key > best_key:
            best_key = key
            best_png = candidate_png
    if best_png:
        return best_png, "largest_embedded_raster"

    target_size = _target_page_size_px(page, dpi=dpi, rotation_deg=rotation_deg)
    rendered = _render_page_with_pillow(
        payload,
        page_index=page_index,
        target_size=target_size,
        rotation_deg=rotation_deg,
    )
    if rendered:
        return rendered, "rendered_page"

    # Last-resort deterministic fallback at fixed DPI. This will fail key extraction quality gate.
    blank = Image.new("RGB", target_size, color=(255, 255, 255))
    return _to_png_bytes(blank), "blank_page_fallback"
