from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
from typing import Optional, Tuple

try:  # pragma: no cover - optional dependency check
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _HAS_CV2 = True
    _CV2_ERR = None
except Exception as exc:  # pragma: no cover - only when missing deps
    cv2 = None  # type: ignore
    np = None  # type: ignore
    _HAS_CV2 = False
    _CV2_ERR = str(exc)

from PIL import Image, ImageOps
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

logger = logging.getLogger(__name__)

MIN_DIM_PX = 50
MAX_DIM_PX = 5000
PDF_DPI = 300
MAX_PDF_IN = 17.0


@dataclass
class ScanResult:
    normalized_png: bytes
    normalized_pdf: bytes
    width_px: int
    height_px: int
    scan_ok: bool
    error: Optional[str] = None


def _order_points(pts: "np.ndarray") -> "np.ndarray":
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _find_page_contour(gray: "np.ndarray") -> Optional["np.ndarray"]:
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blur, 50, 150)
    edged = cv2.dilate(edged, None, iterations=2)
    edged = cv2.erode(edged, None, iterations=1)

    cnts = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = cnts[0] if len(cnts) == 2 else cnts[1]
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for contour in contours:
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) == 4:
            return approx.reshape(4, 2)
    return None


def _four_point_warp(image: "np.ndarray", pts: "np.ndarray") -> "np.ndarray":
    rect = _order_points(pts)
    (tl, tr, br, bl) = rect

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_width = int(max(width_a, width_b))

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_height = int(max(height_a, height_b))

    if max_width <= 0 or max_height <= 0:
        raise ValueError("Invalid page dimensions from contour")

    dst = np.array(
        [
            [0, 0],
            [max_width - 1, 0],
            [max_width - 1, max_height - 1],
            [0, max_height - 1],
        ],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, matrix, (max_width, max_height))


def _deskew(image: "np.ndarray") -> "np.ndarray":
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if coords.size < 100:
        return image

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < 0.3 or abs(angle) > 10:
        return image

    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _normalize_contrast(image: "np.ndarray") -> "np.ndarray":
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l2 = clahe.apply(l)
    merged = cv2.merge((l2, a, b))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def _image_to_png_bytes(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _image_to_pdf_bytes(image: Image.Image) -> bytes:
    width, height = image.size
    width_in = width / PDF_DPI
    height_in = height / PDF_DPI
    max_in = max(width_in, height_in)
    if max_in > MAX_PDF_IN:
        scale = MAX_PDF_IN / max_in
        new_w = max(1, int(round(width * scale)))
        new_h = max(1, int(round(height * scale)))
        image = image.resize((new_w, new_h), Image.LANCZOS)
        width, height = image.size
        width_in = width / PDF_DPI
        height_in = height / PDF_DPI
    width_pt = width_in * 72.0
    height_pt = height_in * 72.0
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width_pt, height_pt))
    c.drawImage(ImageReader(image), 0, 0, width=width_pt, height=height_pt)
    c.showPage()
    c.save()
    return buf.getvalue()


def _resize_dims(width: int, height: int) -> Tuple[int, int, float]:
    if width <= 0 or height <= 0:
        return width, height, 1.0
    max_dim = max(width, height)
    scale_down = min(1.0, MAX_DIM_PX / float(max_dim))
    width = max(1, int(round(width * scale_down)))
    height = max(1, int(round(height * scale_down)))
    min_dim = min(width, height)
    scale_up = 1.0
    if min_dim < MIN_DIM_PX:
        scale_up = MIN_DIM_PX / float(min_dim)
        if max(width, height) * scale_up <= MAX_DIM_PX:
            width = max(1, int(round(width * scale_up)))
            height = max(1, int(round(height * scale_up)))
            return width, height, scale_down * scale_up
        return width, height, scale_down
    return width, height, scale_down


def _pad_pil(image: Image.Image) -> Image.Image:
    width, height = image.size
    if min(width, height) >= MIN_DIM_PX:
        return image
    new_w = max(width, MIN_DIM_PX)
    new_h = max(height, MIN_DIM_PX)
    canvas = Image.new("RGB", (new_w, new_h), color=(255, 255, 255))
    offset = ((new_w - width) // 2, (new_h - height) // 2)
    canvas.paste(image, offset)
    return canvas


def _pad_cv(image: "np.ndarray") -> "np.ndarray":
    height, width = image.shape[:2]
    if min(width, height) >= MIN_DIM_PX:
        return image
    new_w = max(width, MIN_DIM_PX)
    new_h = max(height, MIN_DIM_PX)
    canvas = 255 * np.ones((new_h, new_w, 3), dtype=image.dtype)
    x = (new_w - width) // 2
    y = (new_h - height) // 2
    canvas[y : y + height, x : x + width] = image
    return canvas


def _clamp_pil(image: Image.Image) -> Image.Image:
    width, height = image.size
    new_w, new_h, scale = _resize_dims(width, height)
    if (new_w, new_h) != (width, height):
        resample = Image.LANCZOS if scale < 1.0 else Image.BICUBIC
        image = image.resize((new_w, new_h), resample)
    return _pad_pil(image)


def _clamp_cv(image: "np.ndarray") -> "np.ndarray":
    height, width = image.shape[:2]
    new_w, new_h, scale = _resize_dims(width, height)
    if (new_w, new_h) != (width, height):
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        image = cv2.resize(image, (new_w, new_h), interpolation=interp)
    return _pad_cv(image)


def normalize_image_bytes(image_bytes: bytes) -> ScanResult:
    img = Image.open(BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = _clamp_pil(img)

    if not _HAS_CV2:
        err = f"opencv_missing: {_CV2_ERR}"
        logger.error("Scan normalization skipped: %s", err)
        png_bytes = _image_to_png_bytes(img)
        pdf_bytes = _image_to_pdf_bytes(img)
        width, height = img.size
        logger.info("Normalized image size (fallback): %sx%s px", width, height)
        return ScanResult(
            normalized_png=png_bytes,
            normalized_pdf=pdf_bytes,
            width_px=width,
            height_px=height,
            scan_ok=False,
            error=err,
        )


def image_bytes_to_pdf(image_bytes: bytes) -> bytes:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = _clamp_pil(img)
    return _image_to_pdf_bytes(img)

    try:
        cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        img_h, img_w = cv_img.shape[:2]
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        contour = _find_page_contour(gray)
        if contour is None:
            raise ValueError("page_contour_not_found")
        contour_area = float(cv2.contourArea(contour))
        img_area = float(img_w * img_h) if img_w and img_h else 0.0
        if img_area and (contour_area / img_area) < 0.2:
            raise ValueError("page_contour_too_small")

        warped = _four_point_warp(cv_img, contour)
        warped_h, warped_w = warped.shape[:2]
        if min(warped_w, warped_h) < 200:
            raise ValueError("page_warp_too_small")
        warped = _deskew(warped)
        warped = _normalize_contrast(warped)
        warped = _clamp_cv(warped)
        normalized = Image.fromarray(cv2.cvtColor(warped, cv2.COLOR_BGR2RGB))
        png_bytes = _image_to_png_bytes(normalized)
        pdf_bytes = _image_to_pdf_bytes(normalized)
        width, height = normalized.size
        logger.info("Normalized image size: %sx%s px", width, height)
        return ScanResult(
            normalized_png=png_bytes,
            normalized_pdf=pdf_bytes,
            width_px=width,
            height_px=height,
            scan_ok=True,
            error=None,
        )
    except Exception as exc:
        err = str(exc)
        logger.warning("Scan normalization failed; falling back to EXIF-only: %s", err)
        img = _clamp_pil(img)
        png_bytes = _image_to_png_bytes(img)
        pdf_bytes = _image_to_pdf_bytes(img)
        width, height = img.size
        logger.info("Normalized image size (fallback): %sx%s px", width, height)
        return ScanResult(
            normalized_png=png_bytes,
            normalized_pdf=pdf_bytes,
            width_px=width,
            height_px=height,
            scan_ok=False,
            error=err,
        )
