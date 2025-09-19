from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


QuestionType = Literal["numeric", "MCQ", "short_answer", "show_work"]


class Question(BaseModel):
    id: str
    prompt: str
    qtype: QuestionType
    options: Optional[List[str]] = None  # for MCQ


class AutoKey(BaseModel):
    question_id: str
    answer: str
    must_include: Optional[List[str]] = None  # for short_answer


class CriterionScore(BaseModel):
    name: str
    score: float
    max_score: float
    rationale: str


class QuestionGrade(BaseModel):
    question_id: str
    qtype: QuestionType
    score: float
    max_score: float
    criteria: List[CriterionScore] = Field(default_factory=list)
    rationale: str = ""
    low_confidence: bool = False


class GradeResult(BaseModel):
    submission_id: str
    total_score: float
    total_max: float
    items: List[QuestionGrade] = Field(default_factory=list)
    rubric_version: str
    prompt_version: str
    needs_review: bool = False


class OverlayMark(BaseModel):
    tool: Literal["check", "cross", "bubble", "note", "highlight"]
    coords: List[float]  # x, y [, w, h]
    text: Optional[str] = None


class Overlay(BaseModel):
    page: int = 1
    marks: List[OverlayMark] = Field(default_factory=list)

