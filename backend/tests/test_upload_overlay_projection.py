from backend.api.uploads import _project_template_regions_for_student_overlay


def test_project_template_regions_for_student_overlay_projects_bbox_and_anchor():
    regions_payload = {
        "version": 1,
        "page_index": 0,
        "template_width_px": 1200,
        "template_height_px": 1600,
        "regions": [
            {"qid": "Q1", "bbox_px": [300, 200, 100, 40], "expected_answer_text": "23 R0"},
        ],
    }
    # Homography produced by alignment: student -> template with +100 x and +50 y translation.
    homography = [
        [1.0, 0.0, 100.0],
        [0.0, 1.0, 50.0],
        [0.0, 0.0, 1.0],
    ]

    projected = _project_template_regions_for_student_overlay(
        regions_payload=regions_payload,
        template_size=(1200.0, 1600.0),
        student_size=(1200.0, 1600.0),
        homography=homography,
    )

    assert projected is not None
    regions = projected.get("regions") or []
    assert len(regions) == 1
    q1 = regions[0]
    assert q1.get("qid") == "Q1"
    # Template box (300,200,100,40) should map to student box (200,150,100,40).
    bbox = q1.get("bbox_px") or []
    assert [round(float(v), 1) for v in bbox] == [200.0, 150.0, 100.0, 40.0]
    # Anchor point (x1-18, y0+6)=(382,206) should map to (282,156).
    anchor = q1.get("mark_anchor_px") or []
    assert [round(float(v), 1) for v in anchor] == [282.0, 156.0]
