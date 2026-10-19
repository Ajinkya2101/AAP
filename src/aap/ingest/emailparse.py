"""Email parsing helpers for stage 2 (unpack): uploaded .eml / .msg files."""

from __future__ import annotations

import email
import email.policy
import email.utils
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import bleach
import html2text

# Headers kept on email_message.headers (the raw .eml keeps everything).
EMAIL_HEADER_KEYS = frozenset({"from", "to", "cc", "subject", "date", "message-id", "in-reply-to", "references",
                               "auto-submitted", "x-autoreply", "precedence", "reply-to", "content-type"})

AUTO_REPLY_SUBJECT = re.compile(r"^\s*(automatic reply|auto(-|\s)?reply|out of (the )?office|abwesenheit|"
                                r"autoreply|vacation reply)", re.I)


@dataclass
class ParsedAttachment:
    filename: str | None
    content_type: str
    data: bytes


@dataclass
class ParsedEmail:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    from_addr: str | None
    to_addrs: list[str]
    cc_addrs: list[str]
    subject: str | None
    date: datetime | None
    headers: dict[str, str]
    text_body: str
    attachments: list[ParsedAttachment] = field(default_factory=list)

    @property
    def is_auto_reply(self) -> bool:
        auto = self.headers.get("auto-submitted", "").lower()
        if auto and auto != "no":
            return True
        if self.headers.get("x-autoreply") or self.headers.get("x-autorespond"):
            return True
        if self.headers.get("precedence", "").lower() in ("auto_reply", "bulk", "junk"):
            return True
        return bool(self.subject and AUTO_REPLY_SUBJECT.match(self.subject))


def html_to_text(html: str) -> str:
    """Sanitize (no scripts, no remote content) then convert to plain text."""
    clean = bleach.clean(html, tags=["p", "br", "div", "table", "tr", "td", "th", "tbody", "thead", "span", "b",
                                     "strong", "i", "em", "ul", "ol", "li", "h1", "h2", "h3", "h4", "pre"],
                         attributes={}, strip=True)
    h = html2text.HTML2Text()
    h.ignore_images = True
    h.ignore_links = True
    h.body_width = 0
    return h.handle(clean).strip()


def _addrs(values: list[str] | None) -> list[str]:
    return [addr for _, addr in email.utils.getaddresses(values or []) if addr]


def parse_eml(raw: bytes) -> ParsedEmail:
    msg: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)  # type: ignore[assignment]
    headers = {k.lower(): str(v) for k, v in msg.items()}
    body_part = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body_part is not None:
        content = body_part.get_content()
        text = html_to_text(content) if body_part.get_content_type() == "text/html" else content
    attachments = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True) or b""
        attachments.append(ParsedAttachment(filename=part.get_filename(), content_type=part.get_content_type(),
                                            data=payload))
    date = None
    if msg.get("date"):
        try:
            date = email.utils.parsedate_to_datetime(str(msg["date"]))
        except (TypeError, ValueError):
            date = None
    refs = str(msg.get("references") or "").split()
    from_list = _addrs([str(msg["from"])]) if msg.get("from") else []
    return ParsedEmail(
        message_id=str(msg.get("message-id") or "").strip() or None,
        in_reply_to=str(msg.get("in-reply-to") or "").strip() or None,
        references=refs, from_addr=from_list[0] if from_list else None,
        to_addrs=_addrs([str(v) for v in msg.get_all("to", [])]),
        cc_addrs=_addrs([str(v) for v in msg.get_all("cc", [])]),
        subject=str(msg.get("subject") or "") or None, date=date, headers=headers, text_body=text or "",
        attachments=attachments,
    )


def parse_msg(raw: bytes) -> ParsedEmail:
    """Outlook .msg via extract-msg (license check pending: IP-05)."""
    import extract_msg

    with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as f:
        f.write(raw)
        path = Path(f.name)
    try:
        m = extract_msg.openMsg(str(path))
        try:
            body = m.body or ""
            if not body and getattr(m, "htmlBody", None):
                html = m.htmlBody.decode("utf-8", "replace") if isinstance(m.htmlBody, bytes) else m.htmlBody
                body = html_to_text(html)
            atts = []
            for a in m.attachments:
                data = getattr(a, "data", None)
                if isinstance(data, bytes):
                    atts.append(ParsedAttachment(filename=getattr(a, "longFilename", None) or getattr(a, "shortFilename", None),
                                                 content_type=getattr(a, "mimetype", None) or "application/octet-stream",
                                                 data=data))
            headers = {k.lower(): str(v) for k, v in (m.header.items() if m.header else [])}
            sender = _addrs([m.sender]) if m.sender else []
            return ParsedEmail(
                message_id=headers.get("message-id"), in_reply_to=headers.get("in-reply-to"),
                references=headers.get("references", "").split(), from_addr=sender[0] if sender else None,
                to_addrs=_addrs([m.to]) if m.to else [], cc_addrs=_addrs([m.cc]) if m.cc else [],
                subject=m.subject, date=m.date if isinstance(m.date, datetime) else None, headers=headers,
                text_body=body, attachments=atts)
        finally:
            m.close()
    finally:
        path.unlink(missing_ok=True)
