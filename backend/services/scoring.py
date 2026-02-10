from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..models.schemas import CriterionScore, GradeResult, QuestionGrade
from .llm_grader import LLMAnswer

RUBRIC_VERSION = "quotient-remainder-1"
PROMPT_VERSION = "answer-extract-v1"


def _normalize_remainder_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    # Normalize common OCR/operator variants first.
    raw = raw.replace("÷", "/").replace("\\", "/")
    raw = re.sub(r"(?i)\b(quotient)\b", " ", raw)
    # Treat "RO" (letter O) as "R0" when it appears after R.
    raw = re.sub(r"(?i)r\s*o\b", "R0", raw)
    raw = re.sub(r"(?i)\b(remainder|rem)\b", "R", raw)
    # Repair common OCR digit confusions only in numeric contexts.
    raw = re.sub(r"(?<=\d)[oO](?=\d|\b)", "0", raw)
    raw = re.sub(r"(?<=\d)[lI|](?=\d|\b)", "1", raw)
    raw = re.sub(r"(?i)(?<=r)\s*[oO]\b", "0", raw)
    raw = re.sub(r"(?i)(?<=r)\s*[oO](?=\d)", "0", raw)
    raw = re.sub(r"(?i)(?<=r)\s*[lI|](?=\d|\b)", "1", raw)
    # Keep only symbols useful for quotient/remainder parsing.
    raw = re.sub(r"[^0-9rR/\-\s]", " ", raw)
    raw = " ".join(raw.split())
    return raw


def parse_quotient_remainder(text: str) -> Optional[Tuple[int, int]]:
    normalized = _normalize_remainder_text(text)
    if not normalized:
        return None
    normalized = normalized.upper()
    match = re.search(r"(-?\d+)\s*r\s*(-?\d+)", normalized, flags=re.IGNORECASE)
    if not match:
        slash = re.match(r"^(-?\d+)\s*/\s*(-?\d+)$", normalized)
        if slash:
            try:
                return int(slash.group(1)), int(slash.group(2))
            except Exception:
                return None
        # Handle missing "R" when two numeric groups are present (e.g., "161 03" -> 161 R3).
        fallback = re.match(r"^(-?\d+)\s+0*([0-9]{1,2})$", normalized)
        if not fallback:
            return None
        try:
            return int(fallback.group(1)), int(fallback.group(2))
        except Exception:
            return None
    try:
        return int(match.group(1)), int(match.group(2))
    except Exception:
        return None


def score_quotient_remainder(expected_raw: str, observed_raw: str) -> Tuple[str, float, str, bool]:
    expected_parsed = parse_quotient_remainder(expected_raw)
    observed_parsed = parse_quotient_remainder(observed_raw)
    if expected_parsed is None or observed_parsed is None:
        return "needs_review", 0.0, "Needs review: unable to parse quotient/remainder", True
    if expected_parsed == observed_parsed:
        return "correct", 1.0, "Exact quotient/remainder match", False
    return "incorrect", 0.0, "Quotient/remainder mismatch", False


def _sorted_qids(qids: List[str]) -> List[str]:
    def sort_key(qid: str) -> Tuple[int, str]:
        match = re.search(r"\d+", qid)
        return (int(match.group(0)) if match else 9999, qid)

    return sorted(qids, key=sort_key)


def score_answer_maps(
    key_answers: Dict[str, str],
    student_answers: Dict[str, str],
) -> Tuple[GradeResult, List[LLMAnswer], List[Dict[str, object]]]:
    qids = _sorted_qids(list(key_answers.keys()))
    items: List[QuestionGrade] = []
    answers: List[LLMAnswer] = []
    answer_rows: List[Dict[str, str]] = []
    needs_review = False

    for qid in qids:
        expected = str(key_answers.get(qid) or "").strip()
        got = str(student_answers.get(qid) or "").strip()
        status, score, rationale, low_conf = score_quotient_remainder(expected, got)
        if low_conf:
            needs_review = True
        items.append(
            QuestionGrade(
                question_id=qid,
                qtype="short_answer",
                score=score,
                max_score=1.0,
                criteria=[
                    CriterionScore(
                        name="quotient-remainder",
                        score=score,
                        max_score=1.0,
                        rationale=rationale,
                    )
                ],
                rationale=rationale,
                low_confidence=low_conf,
            )
        )
        answers.append(
            LLMAnswer(
                question_id=qid,
                question="",
                student_answer=got,
                correct=status == "correct",
                confidence=0.0 if low_conf else 1.0,
                rationale=rationale,
            )
        )
        answer_rows.append(
            {
                "question_id": qid,
                "status": status,
                "score": score,
                "expected_raw": expected,
                "observed_raw": got,
            }
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
    return result, answers, answer_rows
