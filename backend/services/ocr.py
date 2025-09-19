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

    def _headers(self, content_type: Optional[str] = None) -> dict:
        h = {}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if content_type:
            h["Content-Type"] = content_type
        return h

    async def extract_text(self, image_bytes: Optional[bytes] = None, image_url: Optional[str] = None) -> dict:
        if not self.api_url:
            raise RuntimeError("HF_API_URL not configured")

        async with httpx.AsyncClient(timeout=60) as client:
            try:
                # Prefer bytes if provided; else try URL via inputs
                if image_bytes is not None:
                    r = await client.post(self.api_url, headers=self._headers("application/octet-stream"), content=image_bytes)
                elif image_url:
                    r = await client.post(self.api_url, headers=self._headers("application/json"), json={"inputs": image_url})
                else:
                    raise ValueError("Either image_bytes or image_url must be provided")

                r.raise_for_status()
                data = r.json()
            except httpx.HTTPStatusError as e:
                msg = f"HF API HTTP error: {e.response.status_code} {e.response.text[:200]}"
                logger.error(msg)
                raise RuntimeError(msg)
            except Exception as e:
                logger.exception("HF API request failed")
                raise

        # Normalize possible response shapes
        text = ""
        pages: Any | None = None
        confidence: float | None = None

        try:
            if isinstance(data, dict):
                # Common shapes: {"text": "..."} or {"generated_text": "..."}
                text = data.get("text") or data.get("generated_text") or ""
            elif isinstance(data, list):
                # Some HF pipelines return a list of {"text": "..."} or {"generated_text": "..."}
                parts = []
                for item in data:
                    if isinstance(item, dict):
                        t = item.get("text") or item.get("generated_text")
                        if t:
                            parts.append(str(t))
                    elif isinstance(item, str):
                        parts.append(item)
                text = "\n".join(parts)
            else:
                text = str(data)
        except Exception:
            # Best-effort fallback
            text = str(data)

        text = (text or "").strip()
        return {"text": text, "pages": pages, "confidence": confidence}


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

