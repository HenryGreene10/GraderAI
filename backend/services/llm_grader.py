from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, List, Tuple

import httpx

from ..models.schemas import CriterionScore, GradeResult, QuestionGrade

logger = logging.getLogger(__name__)

RUBRIC_VERSION = "single-pass"
PROMPT_VERSION = "single-pass-v1"

CONFIDENCE_REVIEW_THRESHOLD = float(os.getenv("LLM_REVIEW_CONFIDENCE", "0.75"))


@dataclass
class LLMAnswer:
    question_id: str
    question: str
    student_answer: str
    correct: bool
    confidence: float
    rationale: str

    def to_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "student_answer": self.student_answer,
            "correct": self.correct,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }


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


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        val = value.strip().lower()
        if val in {"true", "yes", "y", "correct"}:
            return True
        if val in {"false", "no", "n", "incorrect"}:
            return False
    return False


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


async def _call_openrouter(messages: list[dict]) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is required for LLM grading")

    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
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


def _build_prompt(ocr_text: str) -> list[dict]:
    system = (
        "You are a careful grading assistant. Use only the provided OCR text. "
        "Return JSON only with no markdown."
    )
    user = (
        "OCR_TEXT:\n"
        f"{ocr_text}\n\n"
        "Return JSON with this shape:\n"
        "{\n"
        '  "answers": [\n'
        "    {\n"
        '      "question_id": "1",\n'
        '      "question": "question text",\n'
        '      "student_answer": "student answer",\n'
        '      "correct": true,\n'
        '      "confidence": 0.0,\n'
        '      "rationale": "short reason"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Identify each question and the student's answer.\n"
        "- If unsure, set correct=false and confidence <= 0.5 and mention ambiguity.\n"
        "- Use numeric question_id when present; otherwise number sequentially.\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _sanitize_question_id(raw_id: Any, fallback: int) -> str:
    if raw_id is None:
        return str(fallback)
    if isinstance(raw_id, (int, float)):
        return str(int(raw_id))
    text = str(raw_id).strip()
    m = re.search(r"\d+", text)
    return m.group(0) if m else str(fallback)


async def grade_with_llm(ocr_text: str) -> Tuple[GradeResult, List[LLMAnswer]]:
    messages = _build_prompt(ocr_text)
    response_text = await _call_openrouter(messages)
    payload = _extract_json(response_text)

    raw_answers = payload.get("answers") or payload.get("items") or []
    if not isinstance(raw_answers, list) or not raw_answers:
        raise ValueError("LLM returned no answers")

    answers: List[LLMAnswer] = []
    items: List[QuestionGrade] = []
    needs_review = False

    for idx, raw in enumerate(raw_answers, start=1):
        if not isinstance(raw, dict):
            continue
        question_id = _sanitize_question_id(raw.get("question_id"), idx)
        question = str(raw.get("question") or "").strip()
        student_answer = str(raw.get("student_answer") or "").strip()
        correct = _to_bool(raw.get("correct"))
        confidence = _to_float(raw.get("confidence"), 0.0)
        if confidence < 0:
            confidence = 0.0
        if confidence > 1:
            confidence = 1.0
        rationale = str(raw.get("rationale") or "").strip()

        low_confidence = confidence < CONFIDENCE_REVIEW_THRESHOLD
        if low_confidence:
            needs_review = True

        score = 1.0 if correct else 0.0
        max_score = 1.0
        criteria = [
            CriterionScore(
                name="llm",
                score=score,
                max_score=max_score,
                rationale=rationale or "LLM grading",
            )
        ]
        items.append(
            QuestionGrade(
                question_id=question_id,
                qtype="short_answer",
                score=score,
                max_score=max_score,
                criteria=criteria,
                rationale=f"{rationale} (confidence={confidence:.2f})".strip(),
                low_confidence=low_confidence,
            )
        )
        answers.append(
            LLMAnswer(
                question_id=question_id,
                question=question,
                student_answer=student_answer,
                correct=correct,
                confidence=confidence,
                rationale=rationale,
            )
        )

    total = sum(item.score for item in items)
    result = GradeResult(
        submission_id="",
        total_score=total,
        total_max=float(len(items)),
        items=items,
        rubric_version=RUBRIC_VERSION,
        prompt_version=PROMPT_VERSION,
        needs_review=needs_review,
    )
    return result, answers
