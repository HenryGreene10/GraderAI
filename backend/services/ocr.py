import os
import logging
import asyncio
from io import BytesIO
from typing import Any, Optional, Tuple

import httpx
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

OCR_MAX_BYTES = int(3.5 * 1024 * 1024)
OCR_MAX_DIM_PX = 2500
OCR_TARGET_LONG_SIDE = 2400
OCR_JPEG_QUALITY = 80
OCR_DEFAULT_DPI = float(os.getenv("OCR_NORMALIZE_DPI", "300"))


class BaseOCRProvider:
    async def extract_text(self, image_bytes: Optional[bytes] = None, image_url: Optional[str] = None) -> dict:
        raise NotImplementedError


class MockOCRProvider(BaseOCRProvider):
    async def extract_text(self, image_bytes: Optional[bytes] = None, image_url: Optional[str] = None) -> dict:
        text = "[MOCK OCR] Replace with real OCR."
        logger.info("Using MockOCRProvider; returning mock text")
        return {"text": text, "pages": None, "confidence": None}


class HFInferenceOCRProvider(BaseOCRProvider):
    def __init__(self, api_url: str | None = None, token: str | None = None):
        self.api_url = (api_url or os.getenv("HF_API_URL") or "").strip()
        if not self.api_url:
            raise KeyError("HF_API_URL is required for HF OCR provider")
        self.token = token or os.getenv("HF_TOKEN")
        if not self.token:
            raise KeyError("HF_TOKEN is required when OCR_PROVIDER=hf")

    async def extract_text(self, image_bytes: Optional[bytes] = None, image_url: Optional[str] = None) -> dict:
        if image_bytes is None and not image_url:
            raise ValueError("Either image_bytes or image_url must be provided")

        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(timeout=60) as client:
            if image_url:
                resp = await client.post(self.api_url, headers=headers, json={"inputs": image_url})
            else:
                resp = await client.post(self.api_url, headers=headers, content=image_bytes)
            resp.raise_for_status()
            data = resp.json()

        text = _normalize_hf(data)
        return {"text": text, "pages": data, "confidence": None}


class AzureReadOCRProvider(BaseOCRProvider):
    def __init__(self, endpoint: str | None = None, key: str | None = None):
        self.endpoint = (endpoint or os.getenv("AZURE_OCR_ENDPOINT") or "").strip()
        if not self.endpoint:
            raise ValueError("AZURE_OCR_ENDPOINT is required when OCR_PROVIDER=azure")
        self.key = (key or os.getenv("AZURE_OCR_KEY") or "").strip()
        if not self.key:
            raise ValueError("AZURE_OCR_KEY is required when OCR_PROVIDER=azure")

    async def extract_text(self, image_bytes: Optional[bytes] = None, image_url: Optional[str] = None) -> dict:
        if image_bytes is None and not image_url:
            raise ValueError("Either image_bytes or image_url must be provided")

        analyze_url = self.endpoint.rstrip("/") + "/vision/v3.2/read/analyze"
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            if image_url:
                resp = await client.post(analyze_url, headers=headers, json={"url": image_url})
            else:
                resp = await client.post(
                    analyze_url,
                    headers={**headers, "Content-Type": "application/octet-stream"},
                    content=image_bytes,
                )

            if resp.status_code != 202:
                body = (resp.text or "").strip()
                snippet = body[:500]
                raise RuntimeError(
                    f"Azure OCR request failed (status={resp.status_code} body={snippet})"
                )

            op_location = (
                resp.headers.get("Operation-Location")
                or resp.headers.get("operation-location")
                or resp.headers.get("OPERATION-LOCATION")
            )
            if not op_location:
                body = (resp.text or "").strip()
                snippet = body[:500]
                raise RuntimeError(
                    f"Azure OCR missing Operation-Location (status={resp.status_code} body={snippet})"
                )

            for attempt in range(1, 11):
                poll = await client.get(op_location, headers=headers)
                data = poll.json() if poll.content else {}
                status = str(data.get("status") or "").lower()
                if status == "succeeded":
                    text = _normalize_azure_read(data)
                    return {"text": text, "pages": data, "confidence": None}
                if status == "failed":
                    err = data.get("error") or {}
                    code = err.get("code") or "failed"
                    message = err.get("message") or ""
                    raise RuntimeError(f"Azure OCR failed: {code} {message}".strip())
                await asyncio.sleep(0.6)

            raise RuntimeError("Azure OCR timeout waiting for result")


def _inspect_image_bytes(image_bytes: bytes) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            fmt = img.format
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            mime = Image.MIME.get((fmt or "").upper()) if fmt else None
            return width, height, mime
    except Exception:
        return None, None, None


def _prepare_azure_ocr_bytes(image_bytes: bytes) -> Tuple[bytes, Optional[int], Optional[int], Optional[str]]:
    width, height, mime = _inspect_image_bytes(image_bytes)
    bytes_len = len(image_bytes)
    max_dim = max(width or 0, height or 0)
    needs_resize = bytes_len > OCR_MAX_BYTES or (max_dim and max_dim > OCR_MAX_DIM_PX)
    if not needs_resize:
        return image_bytes, width, height, mime

    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            width, height = img.size
            max_dim = max(width, height)
            scale = min(1.0, OCR_TARGET_LONG_SIDE / float(max_dim)) if max_dim else 1.0
            if scale < 1.0:
                new_w = max(1, int(round(width * scale)))
                new_h = max(1, int(round(height * scale)))
                img = img.resize((new_w, new_h), Image.LANCZOS)
                width, height = img.size
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=OCR_JPEG_QUALITY, optimize=True)
            return buf.getvalue(), width, height, "image/jpeg"
    except Exception:
        logger.exception("Failed to downscale Azure OCR input; using original bytes")
        return image_bytes, width, height, mime


def _normalize_hf(json_obj: Any) -> str:
    try:
        if isinstance(json_obj, dict):
            t = json_obj.get("text")
            if isinstance(t, str):
                return t.strip()
            return ""
        if isinstance(json_obj, list):
            parts = []
            for item in json_obj:
                if isinstance(item, dict):
                    t = item.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            return "\n".join(parts).strip()
    except Exception:
        logger.exception("Failed to normalize HF response")
    return ""


def _normalize_azure_read(json_obj: Any) -> str:
    try:
        read_results = (
            (json_obj or {}).get("analyzeResult", {}).get("readResults", [])
        )
        lines = []
        for page in read_results:
            for line in page.get("lines", []):
                text = line.get("text")
                if isinstance(text, str) and text.strip():
                    lines.append(text.strip())
        return "\n".join(lines).strip()
    except Exception:
        logger.exception("Failed to normalize Azure Read response")
    return ""


def _as_float(value: object) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:  # NaN guard
        return None
    return out


def _page_scale_to_px(page: dict, *, dpi: float) -> float:
    unit = str(page.get("unit") or "").strip().lower()
    if unit in {"pixel", "pixels", "px"}:
        return 1.0
    if unit in {"inch", "in", "inches"}:
        return dpi
    if unit in {"centimeter", "centimeters", "cm"}:
        return dpi / 2.54
    if unit in {"millimeter", "millimeters", "mm"}:
        return dpi / 25.4
    if unit in {"point", "points", "pt"}:
        return dpi / 72.0

    # Heuristic fallback for providers that omit unit metadata.
    w = _as_float(page.get("width")) or 0.0
    h = _as_float(page.get("height")) or 0.0
    max_dim = max(w, h)
    if max_dim <= 0:
        return 1.0
    if max_dim <= 17.5:
        return dpi
    if max_dim <= 70.0:
        return dpi / 2.54
    return 1.0


def _scale_bbox_values(values: object, scale: float) -> object:
    if not isinstance(values, list):
        return values
    out = []
    for v in values:
        fv = _as_float(v)
        out.append(fv * scale if fv is not None else v)
    return out


def normalize_ocr_boxes_to_px(ocr_boxes: object, *, dpi: float = OCR_DEFAULT_DPI) -> object:
    if not isinstance(ocr_boxes, dict):
        return ocr_boxes
    analyze = ocr_boxes.get("analyzeResult") or {}
    if not isinstance(analyze, dict):
        return ocr_boxes
    read_results = analyze.get("readResults")
    if not isinstance(read_results, list):
        return ocr_boxes

    out: dict = {
        **ocr_boxes,
        "analyzeResult": {
            **analyze,
            "readResults": [],
        },
    }
    out_pages = out["analyzeResult"]["readResults"]
    for page in read_results:
        if not isinstance(page, dict):
            out_pages.append(page)
            continue
        scale = _page_scale_to_px(page, dpi=dpi)
        page_out = {**page, "unit": "pixel"}

        w = _as_float(page.get("width"))
        h = _as_float(page.get("height"))
        if w is not None:
            page_out["width"] = w * scale
        if h is not None:
            page_out["height"] = h * scale

        lines_out = []
        for line in page.get("lines") or []:
            if not isinstance(line, dict):
                lines_out.append(line)
                continue
            line_out = {**line}
            line_out["boundingBox"] = _scale_bbox_values(line.get("boundingBox"), scale)
            words_out = []
            for word in line.get("words") or []:
                if not isinstance(word, dict):
                    words_out.append(word)
                    continue
                word_out = {**word}
                word_out["boundingBox"] = _scale_bbox_values(word.get("boundingBox"), scale)
                words_out.append(word_out)
            line_out["words"] = words_out
            lines_out.append(line_out)
        page_out["lines"] = lines_out

        marks_out = []
        for mark in page.get("selectionMarks") or []:
            if not isinstance(mark, dict):
                marks_out.append(mark)
                continue
            mark_out = {**mark}
            mark_out["boundingBox"] = _scale_bbox_values(mark.get("boundingBox"), scale)
            marks_out.append(mark_out)
        if marks_out:
            page_out["selectionMarks"] = marks_out

        out_pages.append(page_out)
    return out


def infer_primary_page_size_px(ocr_boxes: object, *, dpi: float = OCR_DEFAULT_DPI) -> tuple[float, float]:
    boxes_px = normalize_ocr_boxes_to_px(ocr_boxes, dpi=dpi)
    if not isinstance(boxes_px, dict):
        return 0.0, 0.0
    analyze = boxes_px.get("analyzeResult") or {}
    if not isinstance(analyze, dict):
        return 0.0, 0.0
    read_results = analyze.get("readResults") or []
    if not isinstance(read_results, list) or not read_results:
        return 0.0, 0.0
    page0 = read_results[0] if isinstance(read_results[0], dict) else {}
    w = _as_float(page0.get("width")) or 0.0
    h = _as_float(page0.get("height")) or 0.0
    if w > 0 and h > 0:
        return w, h
    return 0.0, 0.0


def _provider() -> BaseOCRProvider:
    # Mock override takes precedence
    if os.getenv("OCR_MOCK") == "1":
        logger.info("[ocr] Constructing provider=mock (OCR_MOCK=1)")
        return MockOCRProvider()

    # Default to mock when missing or empty
    provider = os.environ.get("OCR_PROVIDER", "mock").strip().lower() or "mock"
    logger.info("[ocr] Constructing provider=%s", provider)

    if provider == "hf":
        api_url = os.environ.get("HF_API_URL", "").strip()
        token = os.environ.get("HF_TOKEN", "").strip() or None
        # HF provider will raise KeyError if required pieces are missing
        return HFInferenceOCRProvider(api_url=api_url, token=token)
    if provider == "azure":
        endpoint = os.environ.get("AZURE_OCR_ENDPOINT", "").strip()
        key = os.environ.get("AZURE_OCR_KEY", "").strip()
        return AzureReadOCRProvider(endpoint=endpoint, key=key)
    if provider == "mock":
        return MockOCRProvider()

    raise NotImplementedError(f"OCR provider '{provider}' not implemented")


async def extract_text(image_bytes: Optional[bytes] = None, image_url: Optional[str] = None) -> dict:
    provider_name = (
        "mock" if os.getenv("OCR_MOCK") == "1" else (os.environ.get("OCR_PROVIDER", "mock").strip().lower() or "mock")
    )
    prov = _provider()
    ocr_bytes = image_bytes
    width = None
    height = None
    mime = None

    if image_bytes is not None:
        if provider_name == "azure":
            ocr_bytes, width, height, mime = _prepare_azure_ocr_bytes(image_bytes)
        else:
            width, height, mime = _inspect_image_bytes(image_bytes)

    logger.info(
        "[ocr] request provider=%s bytes_len=%s width_px=%s height_px=%s mime=%s",
        provider_name,
        len(ocr_bytes) if ocr_bytes is not None else None,
        width,
        height,
        mime,
    )
    return await prov.extract_text(image_bytes=ocr_bytes, image_url=image_url)


def normalize_ocr_result(raw: dict) -> dict:
    boxes = (raw or {}).get("boxes") or (raw or {}).get("pages")
    return {
        "text": (raw or {}).get("text") or "",
        "boxes": normalize_ocr_boxes_to_px(boxes),
        "confidence": (raw or {}).get("confidence"),
    }
