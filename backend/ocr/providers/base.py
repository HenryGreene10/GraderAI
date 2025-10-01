from typing import Protocol, Dict, Any, Tuple, Optional


class OCRProvider(Protocol):
    def run(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        model_override: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Return (text, meta).
        'text' is normalized UTF-8.
        'meta' may include latency_ms, model, tried, device.
        """
        ...

