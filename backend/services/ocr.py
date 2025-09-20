import os
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


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
    if provider == "mock":
        return MockOCRProvider()

    raise NotImplementedError(f"OCR provider '{provider}' not implemented")


async def extract_text(image_bytes: Optional[bytes] = None, image_url: Optional[str] = None) -> dict:
    prov = _provider()
    return await prov.extract_text(image_bytes=image_bytes, image_url=image_url)
