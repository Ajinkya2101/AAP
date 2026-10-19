"""HTML -> PDF (WeasyPrint), scan noise (image-only PDF), email packaging, truth sidecars."""

from __future__ import annotations

import hashlib
import io
import json
import random
import re
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path
from typing import Any

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageFilter

from .model import InvoiceDoc

TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "j2"]))


def render_html(doc: InvoiceDoc) -> str:
    tpl = _env.get_template(f"{doc.layout}.html.j2")
    return tpl.render(inv=doc.render_context(), labels=doc.labels, lang=doc.lang, accent=doc.accent,
                      page_size=doc.page_size)


def html_to_pdf(html: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=html, base_url=str(TEMPLATES)).write_pdf()


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", s.lower())


def assign_pages(doc: InvoiceDoc, pdf: bytes) -> int:
    """Records on which page each line is printed (needed for >4-page chunking)."""
    with pdfplumber.open(io.BytesIO(pdf)) as p:
        texts = [_norm(page.extract_text() or "") for page in p.pages]
    if len(texts) <= 1:
        return len(texts)
    start = 0
    for ln in doc.lines:
        target = _norm(ln.description)
        for i in range(start, len(texts)):
            if target and target in texts[i]:
                ln.page = i + 1
                start = i
                break
    return len(texts)


def scan_noise(pdf: bytes, rnd: random.Random, dpi: int = 150) -> bytes:
    """Rasterise, tilt, add noise and blur, JPEG-compress, and rebuild an image-only PDF."""
    src = pdfium.PdfDocument(pdf)
    frames: list[Image.Image] = []
    try:
        for page in src:
            img = page.render(scale=dpi / 72).to_pil().convert("L")
            img = img.rotate(rnd.uniform(-1.2, 1.2), resample=Image.BICUBIC, expand=True, fillcolor=255)
            arr = np.asarray(img).astype(np.float32)
            np_rng = np.random.default_rng(rnd.randint(0, 2**31))
            arr += np_rng.normal(0, rnd.uniform(4, 10), arr.shape)
            speckles = np_rng.random(arr.shape) < 0.0008
            arr[speckles] = 0
            img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(rnd.uniform(0.3, 0.8)))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=rnd.randint(55, 75))
            frames.append(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))
    finally:
        src.close()
    out = io.BytesIO()
    frames[0].save(out, format="PDF", save_all=True, append_images=frames[1:], resolution=float(dpi))
    return out.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_email(*, sender: str, to: str, subject: str, body: str, attachments: list[tuple[str, bytes]],
                headers: dict[str, str] | None = None, when: datetime | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = format_datetime(when or datetime.now(UTC))
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(body)
    for filename, data in attachments:
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=filename)
    return bytes(msg)


def write_truth(truth_dirs: list[Path], sha: str, doc: InvoiceDoc, *, file_kind: str, scanned: bool,
                pages: int) -> dict[str, Any]:
    truth = {
        "sha256": sha, "generator": "tools.invoicegen", "layout": doc.layout, "scanned": scanned, "pages": pages,
        "file_kind": file_kind, "source": doc.source_truth(), "expected": doc.expected,
    }
    for d in truth_dirs:
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sha}.truth.json").write_text(json.dumps(truth, indent=1, ensure_ascii=False), encoding="utf-8")
    return truth
