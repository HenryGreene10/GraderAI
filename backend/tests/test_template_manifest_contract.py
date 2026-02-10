import pytest

from backend.services.template_manifest import (
    TEMPLATE_MANIFEST_SCHEMA_V1,
    manifest_from_template_regions,
    load_template_manifest,
    validate_template_manifest,
    with_approved_manifest,
)


def test_manifest_from_template_regions_builds_stable_questions():
    payload = {
        "version": 1,
        "page_index": 0,
        "template_width_px": 1200,
        "template_height_px": 1600,
        "regions": [
            {"qid": "Q2", "bbox_px": [300, 400, 100, 50], "expected_answer_text": "7"},
            {"qid": "Q1", "bbox_px": [100, 200, 90, 40], "expected_answer_text": "5"},
        ],
    }

    manifest = manifest_from_template_regions(
        payload,
        template_version=3,
        template_width_px=1200,
        template_height_px=1600,
    )

    assert manifest.schema_version == TEMPLATE_MANIFEST_SCHEMA_V1
    assert manifest.template_version == 3
    assert manifest.question_count == 2
    assert [q.question_id for q in manifest.questions] == ["Q1", "Q2"]
    assert manifest.questions[0].answer_box_px == [100.0, 200.0, 90.0, 40.0]


def test_validate_template_manifest_rejects_duplicate_question_ids():
    with pytest.raises(ValueError):
        validate_template_manifest(
            {
                "schema_version": TEMPLATE_MANIFEST_SCHEMA_V1,
                "template_version": 1,
                "template_width_px": 1000,
                "template_height_px": 1400,
                "question_count": 2,
                "questions": [
                    {
                        "question_id": "Q1",
                        "page_index": 0,
                        "answer_box_px": [100, 100, 40, 20],
                    },
                    {
                        "question_id": "Q1",
                        "page_index": 0,
                        "answer_box_px": [200, 200, 40, 20],
                    },
                ],
            }
        )


def test_manifest_from_template_regions_requires_answer_box():
    payload = {
        "version": 1,
        "page_index": 0,
        "template_width_px": 1200,
        "template_height_px": 1600,
        "regions": [
            {"qid": "Q1", "expected_answer_text": "5"},
        ],
    }
    with pytest.raises(ValueError):
        manifest_from_template_regions(
            payload,
            template_version=1,
            template_width_px=1200,
            template_height_px=1600,
        )


def test_with_approved_manifest_sets_lock_and_embeds_manifest():
    payload = {
        "version": 1,
        "page_index": 0,
        "template_width_px": 1200,
        "template_height_px": 1600,
        "regions": [
            {"qid": "Q1", "bbox_px": [100, 200, 90, 40], "expected_answer_text": "5"},
        ],
    }
    approved = with_approved_manifest(
        payload,
        template_version=4,
        template_width_px=1200,
        template_height_px=1600,
        approved_at="2026-02-09T00:00:00Z",
    )

    assert approved.get("manifest_locked") is True
    assert approved.get("manifest_approved_at") == "2026-02-09T00:00:00Z"
    manifest = approved.get("manifest") or {}
    assert manifest.get("schema_version") == TEMPLATE_MANIFEST_SCHEMA_V1
    assert manifest.get("template_version") == 4
    assert manifest.get("question_count") == 1


def test_load_template_manifest_requires_approved_when_requested():
    payload = {
        "version": 1,
        "template_width_px": 1200,
        "template_height_px": 1600,
        "regions": [{"qid": "Q1", "bbox_px": [100, 200, 90, 40]}],
    }
    with pytest.raises(ValueError):
        load_template_manifest(
            payload,
            template_version=1,
            template_width_px=1200,
            template_height_px=1600,
            require_approved=True,
        )
