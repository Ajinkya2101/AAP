from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from PIL import Image


class ParseError(RuntimeError):
    pass


@dataclass
class ParsedPage:
    page_no: int
    kind: str                                    # pdf | image | sheet | text
    text: str = ""
    words: list[dict[str, Any]] = field(default_factory=list)   # {text, x0, top, x1, bottom} in points
    tables: list[str] = field(default_factory=list)             # markdown
    image_png: bytes | None = None
    width: float | None = None                   # points (PDF) or pixels (image)
    height: float | None = None
    image_width: int | None = None
    image_height: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()
