import logging
from io import BytesIO
from PIL import Image, ImageDraw


def stamp_pdf(image_bytes: bytes, regions: dict, verdicts: dict) -> bytes:
    import io

    # Open source and ensure RGB; always save a page even if no marks
    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(im)

    # Draw simple marks at centers of first rect for q5/q6a/q6b (if present)
    for q in ("q5", "q6a", "q6b"):
        boxes = (regions or {}).get(q) or []
        if not boxes:
            continue
        try:
            x, y, w, h = boxes[0]
            cx, cy = int(x + w / 2), int(y + h / 2)
        except Exception:
            continue
        verdict = ((verdicts or {}).get(q) or "").strip().lower()
        mark = "✓" if verdict == "correct" else "✗"
        color = (0, 128, 0) if verdict == "correct" else (200, 0, 0)
        try:
            draw.text((cx, cy), mark, fill=color, anchor="mm")
        except TypeError:
            # Pillow without anchor support: approximate center by offset
            draw.text((cx, cy), mark, fill=color)

    buf = BytesIO()
    im.save(buf, format="PDF", resolution=150)
    pdf_bytes = buf.getvalue()
    try:
        logging.getLogger(__name__).info("stamper pdf_bytes=%s", len(pdf_bytes))
    except Exception:
        pass
    if len(pdf_bytes) < 1000:
        raise ValueError("empty_pdf_bytes")
    return pdf_bytes
