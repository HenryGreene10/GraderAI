from types import SimpleNamespace

from backend.services.template_regions import build_template_regions_payload


def test_build_template_regions_payload_not_capped_at_nine():
    regions = []
    for idx in range(12):
        regions.append(
            SimpleNamespace(
                answer_box=(100.0, float(idx * 40), 80.0, 20.0),
                expected_answer_text=f"{idx}",
            )
        )

    payload = build_template_regions_payload(regions, (1000, 1400))
    saved = payload.get("regions") or []

    assert len(saved) == 12
    assert saved[0]["qid"] == "Q1"
    assert saved[-1]["qid"] == "Q12"
