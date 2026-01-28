from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
import re
from typing import List, Tuple

try:  # pragma: no cover - optional dependency check
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore
    np = None  # type: ignore

from PIL import Image

from .scanner import MIN_DIM_PX, MAX_DIM_PX

logger = logging.getLogger(__name__)


@dataclass
class TemplateRegion:
    qid: str
    box: Tuple[float, float, float, float]
    label_text: str
    expected_answer: str
    index: int


def _resize_dims(width: int, height: int) -> Tuple[int, int]:
    if width <= 0 or height <= 0:
        return width, height
    max_dim = max(width, height)
    scale = min(1.0, MAX_DIM_PX / float(max_dim))
    width = max(1, int(round(width * scale)))
    height = max(1, int(round(height * scale)))
    min_dim = min(width, height)
    if min_dim < MIN_DIM_PX:
        up = MIN_DIM_PX / float(min_dim)
        if max(width, height) * up <= MAX_DIM_PX:
            width = max(1, int(round(width * up)))
            height = max(1, int(round(height * up)))
    return width, height


def _normalize_crop(image: Image.Image) -> Image.Image:
    width, height = image.size
    new_w, new_h = _resize_dims(width, height)
    if (new_w, new_h) != (width, height):
        image = image.resize((new_w, new_h), Image.BICUBIC)
    width, height = image.size
    if min(width, height) < MIN_DIM_PX:
        new_w = max(width, MIN_DIM_PX)
        new_h = max(height, MIN_DIM_PX)
        canvas = Image.new("RGB", (new_w, new_h), color=(255, 255, 255))
        offset = ((new_w - width) // 2, (new_h - height) // 2)
        canvas.paste(image, offset)
        return canvas
    return image


def _parse_qid(text: str, fallback: int) -> Tuple[str, str]:
    raw = (text or "").strip()
    match = re.search(r"(?:q\s*)?(\d{1,3})", raw, re.I)
    if match:
        return f"Q{match.group(1)}", raw
    return f"Q{fallback}", raw


def detect_answer_boxes(image_bytes: bytes) -> List[Tuple[float, float, float, float]]:
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV is required for template box detection")
    data = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Failed to decode template image")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 5
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)

    cnts = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = cnts[0] if len(cnts) == 2 else cnts[1]
    img_h, img_w = image.shape[:2]
    img_area = float(img_w * img_h)
    min_area = img_area * 0.005
    max_area = img_area * 0.9

    boxes: List[Tuple[float, float, float, float]] = []
    for contour in contours:
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) != 4:
            continue
        x, y, w, h = cv2.boundingRect(approx)
        area = float(w * h)
        if area < min_area or area > max_area:
            continue
        if w < 40 or h < 30:
            continue
        boxes.append((float(x), float(y), float(x + w), float(y + h)))

    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


async def extract_regions(
    image_bytes: bytes,
    boxes: List[Tuple[float, float, float, float]],
    ocr_func,
) -> List[TemplateRegion]:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    regions: List[TemplateRegion] = []
    for idx, (x0, y0, x1, y1) in enumerate(boxes, start=1):
        label_w = max(40, int((x1 - x0) * 0.25))
        label_h = max(30, int((y1 - y0) * 0.2))
        label_crop = image.crop((x0, y0, min(x1, x0 + label_w), min(y1, y0 + label_h)))
        label_crop = _normalize_crop(label_crop)
        label_bytes = _image_to_png(label_crop)
        label_text = ""
        qid = f"Q{idx}"
        try:
            label_text = await _ocr_text(ocr_func, label_bytes)
            qid, label_text = _parse_qid(label_text, idx)
        except Exception:
            qid = f"Q{idx}"

        answer_crop = image.crop((x0, y0, x1, y1))
        answer_crop = _normalize_crop(answer_crop)
        answer_bytes = _image_to_png(answer_crop)
        expected_answer = ""
        try:
            expected_answer = await _ocr_text(ocr_func, answer_bytes)
        except Exception:
            expected_answer = ""

        regions.append(
            TemplateRegion(
                qid=qid,
                box=(x0, y0, x1, y1),
                label_text=label_text,
                expected_answer=expected_answer,
                index=idx,
            )
        )
    return regions


async def _ocr_text(ocr_func, image_bytes: bytes) -> str:
    raw = ocr_func(image_bytes=image_bytes)
    if hasattr(raw, "__await__"):
        raw = await raw
    text = ""
    if isinstance(raw, dict):
        text = str(raw.get("text") or "").strip()
    return text


def _image_to_png(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
