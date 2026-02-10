from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

from .template_regions import parse_regions_payload

TEMPLATE_MANIFEST_SCHEMA_V1 = "template_manifest.v1"


def _qid_sort_key(qid: str) -> tuple[int, str]:
    digits = "".join(ch for ch in str(qid) if ch.isdigit())
    if digits:
        return int(digits), str(qid)
    return 10_000, str(qid)


def _as_rect4(values: object) -> Optional[List[float]]:
    if isinstance(values, (list, tuple)) and len(values) >= 4:
        try:
            return [
                float(values[0]),
                float(values[1]),
                float(values[2]),
                float(values[3]),
            ]
        except Exception:
            return None
    return None


def _rect_from_answer_box(answer_box: object) -> Optional[List[float]]:
    if not isinstance(answer_box, dict):
        return None
    try:
        x = float(answer_box.get("x") or 0.0)
        y = float(answer_box.get("y") or 0.0)
        w = float(answer_box.get("w") or 0.0)
        h = float(answer_box.get("h") or 0.0)
    except Exception:
        return None
    return [x, y, w, h]


def _coerce_positive_int(value: object) -> int:
    out = int(round(float(value)))
    if out <= 0:
        raise ValueError("must_be_positive")
    return out


def _validate_rect(values: List[float], *, allow_zero_origin: bool = True) -> List[float]:
    if len(values) != 4:
        raise ValueError("box_requires_four_values")
    x, y, w, h = values
    for v in (x, y, w, h):
        if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            raise ValueError("box_values_must_be_finite_numbers")
    if w <= 0 or h <= 0:
        raise ValueError("box_width_height_must_be_positive")
    if not allow_zero_origin and (x <= 0 or y <= 0):
        raise ValueError("box_origin_must_be_positive")
    if x < 0 or y < 0:
        raise ValueError("box_origin_must_be_non_negative")
    return [float(x), float(y), float(w), float(h)]


class TemplateManifestQuestionV1(BaseModel):
    question_id: str = Field(min_length=1)
    page_index: int = Field(default=0, ge=0)
    answer_box_px: List[float] = Field(min_length=4, max_length=4)
    region_box_px: Optional[List[float]] = Field(default=None, min_length=4, max_length=4)
    expected_answer_text: str = ""
    source: str = "template_regions"

    @field_validator("question_id")
    @classmethod
    def _validate_question_id(cls, value: str) -> str:
        qid = str(value or "").strip()
        if not qid:
            raise ValueError("question_id_required")
        if not re.match(r"^[A-Za-z0-9_-]+$", qid):
            raise ValueError("question_id_invalid")
        return qid

    @field_validator("answer_box_px")
    @classmethod
    def _validate_answer_box(cls, value: List[float]) -> List[float]:
        return _validate_rect(value)

    @field_validator("region_box_px")
    @classmethod
    def _validate_region_box(cls, value: Optional[List[float]]) -> Optional[List[float]]:
        if value is None:
            return None
        return _validate_rect(value)


class TemplateManifestV1(BaseModel):
    schema_version: str = TEMPLATE_MANIFEST_SCHEMA_V1
    template_version: int = Field(ge=1)
    template_width_px: int = Field(gt=0)
    template_height_px: int = Field(gt=0)
    question_count: Optional[int] = Field(default=None, ge=1)
    questions: List[TemplateManifestQuestionV1] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_manifest(self) -> "TemplateManifestV1":
        if self.schema_version != TEMPLATE_MANIFEST_SCHEMA_V1:
            raise ValueError("unsupported_schema_version")
        seen: set[str] = set()
        duplicates: List[str] = []
        for q in self.questions:
            if q.question_id in seen:
                duplicates.append(q.question_id)
            seen.add(q.question_id)
        if duplicates:
            raise ValueError(f"duplicate_question_ids:{','.join(sorted(set(duplicates)))}")

        if self.question_count is None:
            self.question_count = len(self.questions)
        elif self.question_count != len(self.questions):
            raise ValueError("question_count_mismatch")
        return self


def validate_template_manifest(payload: Dict[str, Any]) -> TemplateManifestV1:
    return TemplateManifestV1.model_validate(payload)


def manifest_from_template_regions(
    template_regions_payload: object,
    *,
    template_version: int,
    template_width_px: object,
    template_height_px: object,
) -> TemplateManifestV1:
    regions_map, meta = parse_regions_payload(template_regions_payload)
    if not regions_map:
        raise ValueError("template_regions_missing")

    width_raw = template_width_px or meta.get("template_width_px")
    height_raw = template_height_px or meta.get("template_height_px")
    width = _coerce_positive_int(width_raw)
    height = _coerce_positive_int(height_raw)

    questions: List[TemplateManifestQuestionV1] = []
    for qid in sorted((str(k) for k in regions_map.keys()), key=_qid_sort_key):
        entry = regions_map.get(qid) or {}
        page_index = int(entry.get("page_index") or meta.get("page_index") or 0)
        if page_index < 0:
            raise ValueError(f"invalid_page_index:{qid}")

        answer_box = _as_rect4(entry.get("bbox_px"))
        if answer_box is None:
            answer_box = _rect_from_answer_box(entry.get("answer_box"))
        if answer_box is None:
            raise ValueError(f"manifest_missing_answer_box:{qid}")

        region_box = _as_rect4(entry.get("region_box_px")) or _as_rect4(entry.get("region"))
        expected = str(entry.get("expected_answer_text") or "").strip()
        source = str(entry.get("source") or "template_regions")
        questions.append(
            TemplateManifestQuestionV1(
                question_id=qid,
                page_index=page_index,
                answer_box_px=answer_box,
                region_box_px=region_box,
                expected_answer_text=expected,
                source=source,
            )
        )

    return TemplateManifestV1(
        schema_version=TEMPLATE_MANIFEST_SCHEMA_V1,
        template_version=max(1, int(template_version or 1)),
        template_width_px=width,
        template_height_px=height,
        question_count=len(questions),
        questions=questions,
    )


def manifest_to_template_regions_payload(manifest: TemplateManifestV1) -> Dict[str, Any]:
    regions: List[Dict[str, Any]] = []
    for q in manifest.questions:
        entry: Dict[str, Any] = {
            "qid": q.question_id,
            "page_index": q.page_index,
            "bbox_px": list(q.answer_box_px),
            "source": q.source or "template_manifest",
            "expected_answer_text": q.expected_answer_text or "",
        }
        if q.region_box_px is not None:
            entry["region_box_px"] = list(q.region_box_px)
        regions.append(entry)
    return {
        "version": 1,
        "page_index": 0,
        "template_width_px": float(manifest.template_width_px),
        "template_height_px": float(manifest.template_height_px),
        "regions": regions,
        "manifest": manifest.model_dump(),
        "manifest_schema_version": manifest.schema_version,
    }


def with_approved_manifest(
    template_regions_payload: object,
    *,
    template_version: int,
    template_width_px: object,
    template_height_px: object,
    approved_at: str,
) -> Dict[str, Any]:
    manifest = manifest_from_template_regions(
        template_regions_payload,
        template_version=template_version,
        template_width_px=template_width_px,
        template_height_px=template_height_px,
    )
    base_payload = manifest_to_template_regions_payload(manifest)
    base_payload["manifest_approved_at"] = approved_at
    base_payload["manifest_locked"] = True
    return base_payload


def load_template_manifest(
    template_regions_payload: object,
    *,
    template_version: int,
    template_width_px: object,
    template_height_px: object,
    require_approved: bool = False,
) -> Tuple[TemplateManifestV1, bool]:
    payload = template_regions_payload if isinstance(template_regions_payload, dict) else {}
    raw_manifest = payload.get("manifest") if isinstance(payload, dict) else None
    if isinstance(raw_manifest, dict):
        manifest = validate_template_manifest(raw_manifest)
        if require_approved:
            if not payload.get("manifest_locked"):
                raise ValueError("template_manifest_not_locked")
            if not payload.get("manifest_approved_at"):
                raise ValueError("template_manifest_not_approved")
        return manifest, True

    if require_approved:
        raise ValueError("template_manifest_unapproved")

    manifest = manifest_from_template_regions(
        template_regions_payload,
        template_version=template_version,
        template_width_px=template_width_px,
        template_height_px=template_height_px,
    )
    return manifest, False
