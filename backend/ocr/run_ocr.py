import os
from typing import Tuple, Dict, Any, Optional


class ProviderUnavailable(RuntimeError):
    def __init__(self, name: str, msg: str = ""):
        super().__init__(f"{name} unavailable: {msg}")
        self.name = name


def get_provider(name: Optional[str] = None, model_id: Optional[str] = None, **kw):
    """
    Lazily import provider modules to avoid heavy imports (e.g., torch)
    at module import time.
    Returns (provider_name, model_id, provider_instance)
    """
    provider_name = (name or os.getenv("OCR_PROVIDER", "trocr_local") or "trocr_local").lower()
    if provider_name in ("trocr_local", "trocr"):
        try:
            # Import only when actually requested
            from .providers.trocr_local import TrOCRLocal  # type: ignore
        except Exception as e:
            raise ProviderUnavailable("trocr_local", str(e))

        model = model_id or os.getenv("OCR_MODEL", "microsoft/trocr-base-handwritten")
        mode = kw.get("mode") or os.getenv("OCR_MODE", "single")
        return "trocr_local", model, TrOCRLocal(default_model=model, mode=mode)

    raise ValueError(f"unknown ocr provider: {provider_name}")


def normalize(text: str) -> str:
    t = (text or "").replace("\r\n", "\n")
    t = t.strip()
    return t
