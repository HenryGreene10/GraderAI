from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
from typing import Optional

try:  # pragma: no cover
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore
    np = None  # type: ignore

from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class AlignmentResult:
    aligned_png: bytes
    homography: Optional[list[list[float]]]
    ok: bool
    match_count: int
    inliers: int
    error: Optional[str]


def _to_png_bytes(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def align_student_to_template(student_png: bytes, template_png: bytes) -> AlignmentResult:
    if cv2 is None or np is None:
        logger.warning("OpenCV missing; skipping template alignment")
        return AlignmentResult(
            aligned_png=student_png,
            homography=None,
            ok=False,
            match_count=0,
            inliers=0,
            error="opencv_missing",
        )

    try:
        tpl = cv2.imdecode(np.frombuffer(template_png, np.uint8), cv2.IMREAD_COLOR)
        stu = cv2.imdecode(np.frombuffer(student_png, np.uint8), cv2.IMREAD_COLOR)
        if tpl is None or stu is None:
            raise ValueError("decode_failed")

        tpl_gray = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
        stu_gray = cv2.cvtColor(stu, cv2.COLOR_BGR2GRAY)

        orb = cv2.ORB_create(2000)
        kp_tpl, des_tpl = orb.detectAndCompute(tpl_gray, None)
        kp_stu, des_stu = orb.detectAndCompute(stu_gray, None)
        if des_tpl is None or des_stu is None or not kp_tpl or not kp_stu:
            raise ValueError("no_features")

        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        matches = matcher.knnMatch(des_stu, des_tpl, k=2)
        good = []
        for m, n in matches:
            if m.distance < 0.75 * n.distance:
                good.append(m)

        if len(good) < 10:
            raise ValueError("insufficient_matches")

        src_pts = np.float32([kp_stu[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp_tpl[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        if H is None:
            raise ValueError("homography_failed")

        h, w = tpl.shape[:2]
        aligned = cv2.warpPerspective(stu, H, (w, h))
        aligned_img = Image.fromarray(cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB))
        inliers = int(mask.sum()) if mask is not None else 0
        return AlignmentResult(
            aligned_png=_to_png_bytes(aligned_img),
            homography=H.tolist(),
            ok=True,
            match_count=len(good),
            inliers=inliers,
            error=None,
        )
    except Exception as exc:
        logger.warning("Template alignment failed: %s", exc)
        return AlignmentResult(
            aligned_png=student_png,
            homography=None,
            ok=False,
            match_count=0,
            inliers=0,
            error=str(exc),
        )
