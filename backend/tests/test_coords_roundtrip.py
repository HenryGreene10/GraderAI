from backend.services.coords import pdf_to_px, px_to_pdf


def test_px_pdf_roundtrip():
    normalized_size = (1200.0, 1800.0)
    page_size = (600.0, 900.0)
    x_px, y_px = 123.4, 567.8
    x_pt, y_pt = px_to_pdf(x_px, y_px, normalized_size, page_size)
    x_back, y_back = pdf_to_px(x_pt, y_pt, normalized_size, page_size)
    assert abs(x_px - x_back) < 0.01
    assert abs(y_px - y_back) < 0.01
