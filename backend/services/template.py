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
class TemplateRegionV1:
    qid: str
    region: Tuple[float, float, float, float]
    answer_box: Tuple[float, float, float, float]
    expected_answer_text: str
    label_method: str
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


def _image_to_png(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _parse_qid(text: str, fallback: int) -> Tuple[str, bool]:
    raw = (text or "").strip()
    match = re.search(r"\bQ\s*([0-9]{1,2})\b", raw, re.I)
    if match:
        return f"Q{match.group(1)}", True
    return f"Q{fallback}", False


def normalize_answer_text(text: str) -> str:
    raw = " ".join((text or "").split())
    if not raw:
        return ""
    raw = re.sub(r"(?i)\b(r|rem|remainder)\s*([0-9]+)\b", r"R\2", raw)
    raw = re.sub(r"(?i)(\d)\s*R\s*([0-9]+)", r"\1 R\2", raw)
    return raw.strip()


def _load_cv_image(image_bytes: bytes):
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV is required for template detection")
    data = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Failed to decode template image")
    return image


def _find_rects(
    thresh,
    img_area: float,
    min_area_ratio: float,
    max_area_ratio: float,
    min_w: int,
    min_h: int,
    aspect_range: Tuple[float, float],
) -> List[Tuple[float, float, float, float]]:
    cnts = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = cnts[0] if len(cnts) == 2 else cnts[1]
    rects: List[Tuple[float, float, float, float]] = []
    for contour in contours:
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) < 4:
            continue
        x, y, w, h = cv2.boundingRect(approx)
        area = float(w * h)
        if area < img_area * min_area_ratio or area > img_area * max_area_ratio:
            continue
        if w < min_w or h < min_h:
            continue
        aspect = float(w) / float(h) if h else 0.0
        if aspect < aspect_range[0] or aspect > aspect_range[1]:
            continue
        rects.append((float(x), float(y), float(w), float(h)))
    rects.sort(key=lambda r: (r[1], r[0]))
    return rects


def detect_question_regions(image_bytes: bytes) -> List[Tuple[float, float, float, float]]:
    image = _load_cv_image(image_bytes)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 5
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)

    img_h, img_w = image.shape[:2]
    img_area = float(img_w * img_h)
    return _find_rects(
        thresh,
        img_area,
        min_area_ratio=0.06,
        max_area_ratio=0.95,
        min_w=120,
        min_h=120,
        aspect_range=(0.2, 5.0),
    )


def detect_answer_boxes(image_bytes: bytes) -> List[Tuple[float, float, float, float]]:
    image = _load_cv_image(image_bytes)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    thresh = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 3
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)

    img_h, img_w = image.shape[:2]
    img_area = float(img_w * img_h)
    return _find_rects(
        thresh,
        img_area,
        min_area_ratio=0.002,
        max_area_ratio=0.08,
        min_w=40,
        min_h=28,
        aspect_range=(0.3, 6.0),
    )


def _contains(region: Tuple[float, float, float, float], box: Tuple[float, float, float, float]) -> bool:
    rx, ry, rw, rh = region
    bx, by, bw, bh = box
    return bx >= rx and by >= ry and (bx + bw) <= (rx + rw) and (by + bh) <= (ry + rh)


async def extract_template_regions(
    image_bytes: bytes,
    ocr_func,
) -> List[TemplateRegionV1]:
    regions = detect_question_regions(image_bytes)
    if not regions:
        raise ValueError(
            "Couldn't detect question regions. Please draw a dashed outline around each question area."
        )
    answer_boxes = detect_answer_boxes(image_bytes)
    if not answer_boxes:
        raise ValueError(
            "Answer box not found inside region Q?. Please draw a solid box around the final answer inside each region."
        )

    region_to_answer: List[Tuple[Tuple[float, float, float, float], Tuple[float, float, float, float]]] = []
    used_answers: set[int] = set()
    for region in regions:
        inside = [idx for idx, box in enumerate(answer_boxes) if _contains(region, box)]
        if len(inside) == 0:
            raise ValueError(
                "Answer box not found inside region Q?. Please draw a solid box around the final answer inside each region."
            )
        if len(inside) > 1:
            raise ValueError(
                "Multiple answer boxes found inside one region. Use one answer box per question region."
            )
        idx = inside[0]
        if idx in used_answers:
            raise ValueError(
                "Answer box matches multiple regions. Ensure each answer box is inside only one region."
            )
        used_answers.add(idx)
        region_to_answer.append((region, answer_boxes[idx]))

    if len(used_answers) != len(answer_boxes):
        raise ValueError(
            "Answer box not found inside region Q?. Please draw a solid box around the final answer inside each region."
        )

    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    entries: List[TemplateRegionV1] = []
    qid_map: List[Tuple[str, bool]] = []

    for idx, (region, answer_box) in enumerate(region_to_answer, start=1):
        rx, ry, rw, rh = region
        label_w = max(40, int(rw * 0.3))
        label_h = max(30, int(rh * 0.3))
        label_crop = image.crop((rx, ry, rx + label_w, ry + label_h))
        label_crop = _normalize_crop(label_crop)
        label_bytes = _image_to_png(label_crop)
        label_text = ""
        qid = f"Q{idx}"
        parsed = False
        try:
            label_text = await _ocr_text(ocr_func, label_bytes)
            qid, parsed = _parse_qid(label_text, idx)
        except Exception:
            parsed = False
        qid_map.append((qid, parsed))

        bx, by, bw, bh = answer_box
        answer_crop = image.crop((bx, by, bx + bw, by + bh))
        answer_crop = _normalize_crop(answer_crop)
        answer_bytes = _image_to_png(answer_crop)
        expected = ""
        try:
            expected = await _ocr_text(ocr_func, answer_bytes)
        except Exception:
            expected = ""
        expected = normalize_answer_text(expected)

        entries.append(
            TemplateRegionV1(
                qid=qid,
                region=region,
                answer_box=answer_box,
                expected_answer_text=expected,
                label_method="ocr" if parsed else "order",
                index=idx,
            )
        )

    if not all(parsed for _, parsed in qid_map):
        for idx, entry in enumerate(entries, start=1):
            entry.qid = f"Q{idx}"
            entry.label_method = "order"

    return entries


async def _ocr_text(ocr_func, image_bytes: bytes) -> str:
    raw = ocr_func(image_bytes=image_bytes)
    if hasattr(raw, "__await__"):
        raw = await raw
    text = ""
    if isinstance(raw, dict):
        text = str(raw.get("text") or "").strip()
    return text
