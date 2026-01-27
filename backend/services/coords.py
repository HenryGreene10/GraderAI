from __future__ import annotations

from typing import Iterable, Tuple


def px_to_pdf(
    x_px: float,
    y_px: float,
    normalized_size: Tuple[float, float],
    page_size: Tuple[float, float],
) -> Tuple[float, float]:
    norm_w, norm_h = normalized_size
    page_w, page_h = page_size
    if norm_w <= 0 or norm_h <= 0:
        return x_px, y_px
    x_pt = x_px * (page_w / norm_w)
    y_pt = page_h - y_px * (page_h / norm_h)
    return x_pt, y_pt


def rect_px_to_pdf(
    rect_px: Iterable[float],
    normalized_size: Tuple[float, float],
    page_size: Tuple[float, float],
) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect_px
    x0_pt, y0_pt = px_to_pdf(x0, y0, normalized_size, page_size)
    x1_pt, y1_pt = px_to_pdf(x1, y1, normalized_size, page_size)
    x_left = min(x0_pt, x1_pt)
    y_bottom = min(y0_pt, y1_pt)
    width = abs(x1_pt - x0_pt)
    height = abs(y1_pt - y0_pt)
    return x_left, y_bottom, width, height
