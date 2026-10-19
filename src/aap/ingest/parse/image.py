"""PNG / JPG / TIFF: Pillow (multi-page TIFF), EXIF orientation, OpenCV deskew."""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageSequence

from .common import ParsedPage, ParseError, png_bytes

MAX_SIDE = 3000


def deskew(img: Image.Image) -> tuple[Image.Image, float]:
    """Estimate small skew from the text mass and rotate it away (|angle| 0.3-15 deg)."""
    gray = np.array(img.convert("L"))
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 500:
        return img, 0.0
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    # OpenCV >= 4.5 returns angles in (0, 90]; map to a small signed correction.
    if angle > 45:
        angle = angle - 90
    if abs(angle) < 0.3 or abs(angle) > 15:
        return img, 0.0
    return img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor="white"), float(angle)


def parse_image(data: bytes) -> list[ParsedPage]:
    try:
        src = Image.open(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"cannot read image: {exc}") from exc
    pages = []
    for i, frame in enumerate(ImageSequence.Iterator(src), start=1):
        img = ImageOps.exif_transpose(frame.convert("RGB"))
        if max(img.size) > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE))
        img, angle = deskew(img)
        pages.append(ParsedPage(page_no=i, kind="image", image_png=png_bytes(img), width=float(img.width),
                                height=float(img.height), image_width=img.width, image_height=img.height,
                                meta={"deskew_angle": round(angle, 2)} if angle else {}))
    return pages
