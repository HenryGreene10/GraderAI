import os
import logging
import asyncio
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
    prov = _provider()
    return await prov.extract_text(image_bytes=image_bytes, image_url=image_url)


def normalize_ocr_result(raw: dict) -> dict:
    return {
        "text": (raw or {}).get("text") or "",
        "boxes": (raw or {}).get("boxes") or (raw or {}).get("pages"),
        "confidence": (raw or {}).get("confidence"),
    }
