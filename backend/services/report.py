from __future__ import annotations

from io import BytesIO
from typing import List

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from ..models.schemas import Overlay, OverlayMark, GradeResult


def build_overlay_basic(result: GradeResult) -> Overlay:
    marks: List[OverlayMark] = []
    y = 720.0
    for idx, item in enumerate(result.items, start=1):
        label = "✓" if item.score >= item.max_score * 0.5 else "✗"
        marks.append(OverlayMark(tool="bubble", coords=[40.0, y], text=f"{item.score:.0f}/{item.max_score:.0f}"))
        marks.append(OverlayMark(tool="note", coords=[90.0, y], text=f"Q{item.question_id}: {label} {item.rationale}"))
        y -= 28.0
    if result.needs_review:
        marks.append(OverlayMark(tool="highlight", coords=[36.0, 40.0, 540.0, 20.0], text="Needs review"))
    return Overlay(page=1, marks=marks)


def flatten_to_pdf(summary_text: str, overlay: Overlay) -> bytes:
    """
    Minimal placeholder PDF summarizing grades and overlay notes.
    This does NOT draw over the original submission; it produces a summary page.
    """
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    c.setTitle("Graded Summary")

    c.setFont("Helvetica-Bold", 16)
    c.drawString(36, height - 48, "Graded Summary")

    c.setFont("Helvetica", 10)
    y = height - 72
    for line in summary_text.splitlines():
        c.drawString(36, y, line[:1000])
        y -= 14
        if y < 60:
            c.showPage()
            y = height - 36

    # Overlay marks list
    c.showPage()
    c.setFont("Helvetica-Bold", 12)
    c.drawString(36, height - 48, "Overlay Marks")
    c.setFont("Helvetica", 10)
    y = height - 72
    for m in overlay.marks:
        txt = f"{m.tool} @ {m.coords} : {m.text or ''}"
        c.drawString(36, y, txt[:1000])
        y -= 14
        if y < 60:
            c.showPage()
            y = height - 36

    c.save()
    return buf.getvalue()

