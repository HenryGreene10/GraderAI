import io
import os
import time
from typing import Dict, Any, Tuple, Optional

import torch
from PIL import Image
import fitz  # PyMuPDF
from transformers import pipeline

PRINTED = "microsoft/trocr-base-printed"
HANDWRITTEN = "microsoft/trocr-base-handwritten"


def _first_page_to_image(pdf_bytes: bytes) -> Image.Image:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return img


def _bytes_to_image(file_bytes: bytes, filename: str) -> Image.Image:
    lower = (filename or "").lower()
    if lower.endswith(".pdf"):
        return _first_page_to_image(file_bytes)
    im = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    return im


def _device() -> str:
    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


class TrOCRLocal:
    _pipelines: Dict[str, Any] = {}

    def __init__(self, default_model: Optional[str] = None, mode: str = "single"):
        self.default_model = default_model or os.getenv("OCR_MODEL", HANDWRITTEN)
        self.mode = os.getenv("OCR_MODE", mode).lower()
        self.dev = _device()

    def _get_pipe(self, model_id: str):
        key = f"{model_id}:{self.dev}"
        if key not in self._pipelines:
            self._pipelines[key] = pipeline(
                "image-to-text",
                model=model_id,
                device=0 if self.dev == "cuda" else -1,
            )
        return self._pipelines[key]

    def _run_once(self, img: Image.Image, model_id: str) -> Tuple[str, Dict[str, Any]]:
        t0 = time.perf_counter()
        pipe = self._get_pipe(model_id)
        # no autocast; CPU-safe
        out = pipe(img)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        text = ""
        if isinstance(out, list) and out and isinstance(out[0], dict):
            text = out[0].get("generated_text", "") or ""
        meta = {
            "latency_ms": latency_ms,
            "model": model_id,
            "device": self.dev,
            "raw_len": len(text),
        }
        return text, meta

    def run(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        model_override: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        img = _bytes_to_image(file_bytes, filename)
        tried = []
        mode = self.mode
        if model_override:
            text, meta = self._run_once(img, model_override)
            meta["tried"] = [model_override]
            return text, meta

        if mode == "auto":
            ordered = (
                [PRINTED, HANDWRITTEN]
                if self.default_model == PRINTED
                else [HANDWRITTEN, PRINTED]
            )
            for mid in ordered:
                text, meta = self._run_once(img, mid)
                tried.append(
                    {
                        "model": mid,
                        "text_len": len(text),
                        "latency_ms": meta["latency_ms"],
                    }
                )
                if len(text.strip()) >= 4:
                    meta["tried"] = tried
                    return text, meta
            meta["tried"] = tried
            return text, meta
        else:
            text, meta = self._run_once(img, self.default_model)
            meta["tried"] = [self.default_model]
            return text, meta

