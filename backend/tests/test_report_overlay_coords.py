from backend.models.schemas import Overlay, OverlayMark
from backend.services import report as report_mod


class _CanvasSpy:
    def __init__(self):
        self.rect_calls = []
        self.draw_calls = []
        self.line_calls = []

    def setFont(self, *_args, **_kwargs):
        return None

    def setFillColorRGB(self, *_args, **_kwargs):
        return None

    def setStrokeColorRGB(self, *_args, **_kwargs):
        return None

    def setLineWidth(self, *_args, **_kwargs):
        return None

    def setLineCap(self, *_args, **_kwargs):
        return None

    def line(self, x1, y1, x2, y2):
        self.line_calls.append((x1, y1, x2, y2))

    def rect(self, x, y, w, h, **_kwargs):
        self.rect_calls.append((x, y, w, h))

    def drawString(self, x, y, text):
        self.draw_calls.append((x, y, text))


def test_draw_marks_converts_px_to_pdf():
    overlay = Overlay(
        page=1,
        marks=[
            OverlayMark(tool="note", coords=[100.0, 200.0], text="hi"),
            OverlayMark(tool="bubble", coords=[300.0, 400.0, 50.0, 60.0], text="Score: 1/1"),
        ],
    )

    canvas = _CanvasSpy()
    report_mod._draw_marks(
        canvas,
        overlay,
        normalized_size_px=(3000.0, 4000.0),
        page_size_pt=(612.0, 792.0),
    )
    def _close(a, b, tol=0.01):
        return abs(a - b) <= tol

    # 100px -> 20.4pt (612/3000), 200px -> 792 - (200*792/4000) = 752.4
    assert any(_close(x, 20.4) and _close(y, 752.4) and text == "hi" for x, y, text in canvas.draw_calls)
    # 300px -> 61.2pt, y=(400+60) -> 792 - (460*792/4000)=700.92, w=10.2, h=11.88
    assert any(
        _close(x, 61.2) and _close(y, 700.92) and _close(w, 10.2) and _close(h, 11.88)
        for x, y, w, h in canvas.rect_calls
    )


def test_overlay_marks_by_page_prefers_meta_payload():
    overlay = Overlay(
        page=1,
        marks=[OverlayMark(tool="note", coords=[1.0, 1.0], text="p1")],
        meta={
            "coords_space": "pt",
            "marks_by_page": {
                "1": [{"tool": "note", "coords": [10.0, 10.0], "text": "first"}],
                "2": [{"tool": "note", "coords": [20.0, 20.0], "text": "second"}],
            },
        },
    )
    marks = report_mod._overlay_marks_by_page(overlay)
    assert 1 in marks and 2 in marks
    assert marks[1][0].text == "first"
    assert marks[2][0].text == "second"
