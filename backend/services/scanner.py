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
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.drawImage(ImageReader(image), 0, 0, width=width, height=height)
    c.showPage()
    c.save()
    return buf.getvalue()


def normalize_image_bytes(image_bytes: bytes) -> ScanResult:
    img = Image.open(BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img).convert("RGB")

    if not _HAS_CV2:
        err = f"opencv_missing: {_CV2_ERR}"
        logger.error("Scan normalization skipped: %s", err)
        png_bytes = _image_to_png_bytes(img)
        pdf_bytes = _image_to_pdf_bytes(img)
        width, height = img.size
        return ScanResult(
            normalized_png=png_bytes,
            normalized_pdf=pdf_bytes,
            width_px=width,
            height_px=height,
            scan_ok=False,
            error=err,
        )

    try:
        cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        contour = _find_page_contour(gray)
        if contour is None:
            raise ValueError("page_contour_not_found")

        warped = _four_point_warp(cv_img, contour)
        warped = _deskew(warped)
        warped = _normalize_contrast(warped)
        normalized = Image.fromarray(cv2.cvtColor(warped, cv2.COLOR_BGR2RGB))
        png_bytes = _image_to_png_bytes(normalized)
        pdf_bytes = _image_to_pdf_bytes(normalized)
        width, height = normalized.size
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
        png_bytes = _image_to_png_bytes(img)
        pdf_bytes = _image_to_pdf_bytes(img)
        width, height = img.size
        return ScanResult(
            normalized_png=png_bytes,
            normalized_pdf=pdf_bytes,
            width_px=width,
            height_px=height,
            scan_ok=False,
            error=err,
        )
