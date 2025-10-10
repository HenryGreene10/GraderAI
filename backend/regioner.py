import logging


def infer_regions(ocr_boxes: dict) -> dict:
    """
    Build three heuristic regions from Azure OCR boxes:
      - Anchor on first line starting with "5"/"5." and "6"/"6." (normalized)
      - Compute page bounds from all line rects
      - q5 spans between 5 and 6 anchors (with small padding)
      - q6 splits the area below 6 into left/right halves
    Returns {"q5":[(x,y,w,h)], "q6a":[...], "q6b":[...]}
    """
    log = logging.getLogger(__name__)

    page = (ocr_boxes.get("pages") or [{}])[0]
    lines = page.get("lines", [])

    def norm(t):
        return (t or "").strip().lower()

    def starts5(t):
        t0 = norm(t)
        return t0.startswith("5.") or t0.startswith("5")

    def starts6(t):
        t0 = norm(t)
        return t0.startswith("6.") or t0.startswith("6")

    # Collect rects and anchors
    rects = []
    anchor5 = None
    anchor6 = None
    for ln in lines:
        bx = ln.get("bbox") or [0, 0, 0, 0]
        try:
            x, y, w, h = [float(v) for v in bx[:4]]
        except Exception:
            x, y, w, h = 0.0, 0.0, 0.0, 0.0
        rects.append((x, y, w, h))
        t = ln.get("text") or ""
        if anchor5 is None and starts5(t):
            anchor5 = (x, y, w, h)
        if anchor6 is None and starts6(t):
            anchor6 = (x, y, w, h)

    # Page bounds from all rects (fallback to provided width/height)
    if rects:
        minX = min(x for (x, y, w, h) in rects)
        minY = min(y for (x, y, w, h) in rects)
        maxX = max(x + w for (x, y, w, h) in rects)
        maxY = max(y + h for (x, y, w, h) in rects)
    else:
        w = float(ocr_boxes.get("width") or page.get("width") or 2000)
        h = float(ocr_boxes.get("height") or page.get("height") or 2800)
        minX, minY, maxX, maxY = 0.0, 0.0, w, h

    pad = 10.0
    midX = (minX + maxX) / 2.0

    # Compute q5 vertical bounds
    if anchor5:
        a5_top = anchor5[1]
        a5_bot = anchor5[1] + anchor5[3]
    else:
        # fallback to upper-middle start
        a5_top = minY + 0.2 * (maxY - minY)
        a5_bot = a5_top

    if anchor6:
        a6_top = anchor6[1]
        a6_bot = anchor6[1] + anchor6[3]
    else:
        # fallback to later section
        a6_top = minY + 0.6 * (maxY - minY)
        a6_bot = a6_top

    q5_top = max(minY, a5_bot + pad)
    q5_bot = max(minY, min(maxY, a6_top - pad))
    q5_left = minX + pad
    q5_right = maxX - pad
    q5_w = max(0.0, q5_right - q5_left)
    q5_h = max(0.0, q5_bot - q5_top)
    q5 = (int(q5_left), int(q5_top), int(q5_w), int(q5_h))

    # Compute q6 area beneath anchor6 (or fallback lower-half)
    q6_top = max(minY, a6_bot + pad)
    q6_bot = max(minY, maxY - pad)
    q6_left = minX + pad
    q6_right = maxX - pad
    # Left/right halves split at midX
    left_w = max(0.0, midX - q6_left)
    right_w = max(0.0, q6_right - midX)
    height_q6 = max(0.0, q6_bot - q6_top)
    q6a = (int(q6_left), int(q6_top), int(left_w), int(height_q6))
    q6b = (int(midX), int(q6_top), int(right_w), int(height_q6))

    try:
        log.info(
            "infer_regions bounds minY=%.1f a5_bot=%.1f a6_top=%.1f a6_bot=%.1f midX=%.1f",
            minY, a5_bot, a6_top, a6_bot, midX,
        )
    except Exception:
        pass

    return {"q5": [q5], "q6a": [q6a], "q6b": [q6b]}
