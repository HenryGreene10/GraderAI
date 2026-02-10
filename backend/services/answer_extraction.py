from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Tuple

import httpx

ANSWER_PROMPT_VERSION = "answer-extract-v1"


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM did not return JSON")
    snippet = text[start : end + 1]
    return json.loads(snippet)


def _sanitize_qid(raw: Any, fallback: int) -> str:
    if raw is None:
        return f"Q{fallback}"
    if isinstance(raw, (int, float)):
        return f"Q{int(raw)}"
    text = str(raw).strip()
    match = re.search(r"\d+", text)
    if match:
        return f"Q{match.group(0)}"
    return f"Q{fallback}"


def _normalize_answers(payload: dict) -> Dict[str, str]:
    raw_answers = payload.get("answers") if isinstance(payload, dict) else None
    if raw_answers is None and isinstance(payload, dict):
        raw_answers = payload
    if not isinstance(raw_answers, dict):
        raise ValueError("LLM returned invalid answers payload")

    normalized: Dict[str, str] = {}
    idx = 1
    for key, value in raw_answers.items():
        qid = _sanitize_qid(key, idx)
        answer = "" if value is None else str(value).strip()
        if qid in normalized:
            idx += 1
            qid = _sanitize_qid(key, idx)
        normalized[qid] = answer
        idx += 1
    return normalized


def _build_prompt(ocr_text: str, role: str) -> list[dict]:
    system = (
        "You extract answers from OCR text. Return JSON only, with no markdown."
    )
    if role == "answer_key":
        task = "Extract the correct answers for each question."
    else:
        task = "Extract the student's answers for each question."

    user = (
        f"{task}\n\n"
        "OCR_TEXT:\n"
        f"{ocr_text}\n\n"
        "Return JSON with this exact shape:\n"
        "{\n"
        '  "answers": {\n'
        '    "Q1": "answer text",\n'
        '    "Q2": "answer text"\n'
        "  }\n"
        "}\n\n"
        "Rules:\n"
        "- Always use Q-prefixed numeric ids (Q1, Q2, Q3...).\n"
        "- Do not include extra keys or commentary.\n"
        "- If an answer is missing, return an empty string.\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def _call_openrouter(messages: list[dict]) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is required for answer extraction")

    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise ValueError("LLM response missing content")


async def extract_answers_from_ocr(ocr_text: str, *, role: str) -> Tuple[Dict[str, str], str]:
    messages = _build_prompt(ocr_text, role=role)
    response_text = await _call_openrouter(messages)
    payload = _extract_json(response_text)
    answers = _normalize_answers(payload)
    return answers, ANSWER_PROMPT_VERSION
