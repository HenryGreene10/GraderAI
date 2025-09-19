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
    def __init__(self, api_url: str, token: str | None):
        self.api_url = api_url
        self.token = token

    async def extract_text(self, image_bytes: Optional[bytes] = None, image_url: Optional[str] = None) -> dict:
        if not self.api_url:
            raise RuntimeError("HF_API_URL not configured")

        # Read token from environment (required)
        try:
            token = os.environ["HF_TOKEN"]
        except KeyError:
            msg = "HF_TOKEN environment variable is required for HF provider"
            logger.error(msg)
            raise KeyError(msg)

        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=60) as client:
            try:
                if image_url:
                    resp = await client.post(self.api_url, headers=headers, json={"inputs": image_url})
                elif image_bytes is not None:
                    resp = await client.post(self.api_url, headers=headers, content=image_bytes)
                else:
                    raise ValueError("Either image_bytes or image_url must be provided")

                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                logger.error("HF API HTTP error %s: %s", getattr(e.response, "status_code", "?"), getattr(e.response, "text", "")[:200])
                raise
            except Exception:
                logger.exception("HF API request failed")
                raise

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
    if os.environ.get("OCR_MOCK", "0") == "1":
        return MockOCRProvider()

    provider = os.environ.get("OCR_PROVIDER", "hf").strip().lower() or "hf"
    if provider == "hf":
        api_url = os.environ.get("HF_API_URL", "").strip()
        token = os.environ.get("HF_TOKEN", "").strip() or None
        return HFInferenceOCRProvider(api_url, token)

    raise NotImplementedError(f"OCR provider '{provider}' not implemented")


async def extract_text(image_bytes: Optional[bytes] = None, image_url: Optional[str] = None) -> dict:
    prov = _provider()
    return await prov.extract_text(image_bytes=image_bytes, image_url=image_url)
