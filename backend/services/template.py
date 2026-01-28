from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
import re
from typing import Dict, List, Optional, Tuple

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


class TemplateValidationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        detail: Optional[Dict[str, object]] = None,
        debug: Optional[Dict[str, object]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {"code": code}
        self.debug = debug or {}


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


def _odd_kernel(size: int) -> int:
    if size % 2 == 0:
        size += 1
    return max(3, size)


def _adaptive_kernel(min_dim: int, ratio: float, min_size: int, max_size: int) -> int:
    size = int(round(min_dim * ratio))
    size = max(min_size, min(size, max_size))
    return _odd_kernel(size)


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
    block = _adaptive_kernel(min(gray.shape[:2]), 0.06, 21, 51)
    thresh = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 5
    )
    kernel_size = _adaptive_kernel(min(gray.shape[:2]), 0.02, 7, 31)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)

    img_h, img_w = image.shape[:2]
    img_area = float(img_w * img_h)
    min_w = max(60, int(round(img_w * 0.18)))
    min_h = max(60, int(round(img_h * 0.12)))
    rects = _find_rects(
        thresh,
        img_area,
        min_area_ratio=0.03,
        max_area_ratio=0.98,
        min_w=min_w,
        min_h=min_h,
        aspect_range=(0.2, 5.0),
    )
    if rects:
        return rects

    edges = cv2.Canny(blur, 50, 150)
    edges = cv2.dilate(edges, None, iterations=2)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=1)
    return _find_rects(
        edges,
        img_area,
        min_area_ratio=0.02,
        max_area_ratio=0.98,
        min_w=min_w,
        min_h=min_h,
        aspect_range=(0.2, 5.5),
    )


def detect_answer_boxes(image_bytes: bytes) -> List[Tuple[float, float, float, float]]:
    image = _load_cv_image(image_bytes)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    block = _adaptive_kernel(min(gray.shape[:2]), 0.05, 15, 45)
    thresh = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 3
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)

    img_h, img_w = image.shape[:2]
    img_area = float(img_w * img_h)
    min_w = max(24, int(round(img_w * 0.06)))
    min_h = max(18, int(round(img_h * 0.04)))
    rects = _find_rects(
        thresh,
        img_area,
        min_area_ratio=0.001,
        max_area_ratio=0.12,
        min_w=min_w,
        min_h=min_h,
        aspect_range=(0.3, 6.0),
    )
    if rects:
        return rects

    edges = cv2.Canny(blur, 50, 150)
    edges = cv2.dilate(edges, None, iterations=1)
    return _find_rects(
        edges,
        img_area,
        min_area_ratio=0.0008,
        max_area_ratio=0.15,
        min_w=min_w,
        min_h=min_h,
        aspect_range=(0.2, 7.0),
    )


def _contains(region: Tuple[float, float, float, float], box: Tuple[float, float, float, float]) -> bool:
    rx, ry, rw, rh = region
    bx, by, bw, bh = box
    return bx >= rx and by >= ry and (bx + bw) <= (rx + rw) and (by + bh) <= (ry + rh)


def _area(box: Tuple[float, float, float, float]) -> float:
    return float(box[2] * box[3])


async def extract_template_regions(
    image_bytes: bytes,
    ocr_func,
    *,
    image_size: Optional[Tuple[int, int]] = None,
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
    for region_index, region in enumerate(regions, start=1):
        inside = [idx for idx, box in enumerate(answer_boxes) if _contains(region, box)]
        if len(inside) == 0:
            raise ValueError(
                "Answer box not found inside region Q?. Please draw a solid box around the final answer inside each region."
            )
        if len(inside) > 1:
            region_area = _area(region)
            filtered = []
            for idx in inside:
                ratio = _area(answer_boxes[idx]) / region_area if region_area else 0.0
                if ratio <= 0.6:
                    filtered.append(idx)
            if len(filtered) == 1:
                inside = filtered
        if len(inside) > 1:
            debug = {
                "image_size": image_size,
                "regions_count": len(regions),
                "region_index": region_index,
                "region": region,
                "answer_boxes_count": len(inside),
                "answer_boxes": [answer_boxes[idx] for idx in inside],
            }
            raise TemplateValidationError(
                "Multiple answer boxes found inside one region. Use one answer box per question region.",
                code="MULTIPLE_ANSWER_BOXES",
                detail={
                    "code": "MULTIPLE_ANSWER_BOXES",
                    "region_index": region_index,
                    "answer_boxes_count": len(inside),
                },
                debug=debug,
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
