"""Turn a Gmail API message resource into ticket fields.

Pure: no network, no DB. Only labeled mail gets here, so volume is small; the body cap
keeps each ticket description small because descriptions reach LLM clients through MCP.
"""

import base64
import html
import re
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser

from app.services import TITLE_MAX_LENGTH

EMAIL_BODY_MAX_CHARS = 2000  # starting value; tune with real marketing mail
SENDER_MAX_LENGTH = 255

_SUBJECT_PREFIX = re.compile(r"^\s*(?:(?:re|fwd?)\s*:\s*)+", re.IGNORECASE)
# ponytail: English reply header only ("On <date>, <name> wrote:", which Gmail may wrap onto
# a second line); add localized patterns if the team's mail clients use another locale.
_REPLY_HEADER = re.compile(r"^On\b[^\n]*(?:\n[^\n]*)?\bwrote:[ \t]*$", re.MULTILINE)
_CHARSET = re.compile(r'charset="?([\w.:-]+)', re.IGNORECASE)


@dataclass(frozen=True)
class GmailAttachmentRef:
    filename: str
    mime_type: str
    size: int
    attachment_id: str


@dataclass(frozen=True)
class ParsedEmail:
    title: str
    body: str
    sender: str
    attachments: tuple[GmailAttachmentRef, ...] = ()

    @property
    def description(self) -> str:
        return "\n\n".join(part for part in (f"From: {self.sender}", self.body) if part)


class _TextExtractor(HTMLParser):
    _SKIP = {"head", "title", "style", "script", "blockquote"}
    _BREAK = {"br", "p", "div", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BREAK:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skipping = max(0, self._skipping - 1)
        elif tag in self._BREAK:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self._skipping:
            self.chunks.append(data)


def _html_to_text(markup: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    return "".join(extractor.chunks)


def _header(headers: list[dict], name: str) -> str:
    wanted = name.lower()
    return next(
        (h.get("value") or "" for h in headers if (h.get("name") or "").lower() == wanted),
        "",
    )


def _parts(part: dict) -> Iterator[dict]:
    yield part
    for child in part.get("parts") or []:
        yield from _parts(child)


def _is_inline(part: dict) -> bool:
    """Signature logos and pasted-in images: inline parts the HTML body refers to by Content-ID."""
    headers = part.get("headers") or []
    disposition = _header(headers, "Content-Disposition").strip().lower()
    return disposition.startswith("inline") and bool(_header(headers, "Content-ID"))


def _attachment_refs(payload: dict) -> tuple[GmailAttachmentRef, ...]:
    return tuple(
        GmailAttachmentRef(
            filename=part["filename"],
            mime_type=part.get("mimeType") or "",
            size=int(part["body"].get("size") or 0),
            attachment_id=part["body"]["attachmentId"],
        )
        for part in _parts(payload)
        if part.get("filename")
        and (part.get("body") or {}).get("attachmentId")
        and not _is_inline(part)
    )


def _decode(part: dict) -> str:
    data = (part.get("body") or {}).get("data") or ""
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    match = _CHARSET.search(_header(part.get("headers") or [], "Content-Type"))
    try:
        return raw.decode(match.group(1) if match else "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _raw_body(payload: dict) -> str:
    parts = [part for part in _parts(payload) if not part.get("filename")]
    for mime_type in ("text/plain", "text/html"):
        part = next(
            (
                p
                for p in parts
                if p.get("mimeType") == mime_type and (p.get("body") or {}).get("data")
            ),
            None,
        )
        if part is not None:
            text = _decode(part)
            return _html_to_text(text) if mime_type == "text/html" else text
    return ""


def _collapse(text: str) -> str:
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _strip_quoted(text: str) -> str:
    match = _REPLY_HEADER.search(text)
    if match:
        text = text[: match.start()]
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith(">"))


def _truncate(body: str) -> str:
    if len(body) <= EMAIL_BODY_MAX_CHARS:
        return body
    omitted = len(body) - EMAIL_BODY_MAX_CHARS
    return f"{body[:EMAIL_BODY_MAX_CHARS].rstrip()}\n\n…(truncated, {omitted} chars omitted)"


def parse_message(message: dict) -> ParsedEmail:
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    sender = " ".join(_header(headers, "From").split())[:SENDER_MAX_LENGTH] or "unknown sender"
    subject = " ".join(_SUBJECT_PREFIX.sub("", _header(headers, "Subject")).split())
    title = (subject or f"(no subject) from {sender}")[:TITLE_MAX_LENGTH]
    raw = _collapse(_raw_body(payload).replace("\r\n", "\n"))
    body = _collapse(_strip_quoted(raw)) or raw or html.unescape(message.get("snippet") or "")
    return ParsedEmail(
        title=title, body=_truncate(body), sender=sender, attachments=_attachment_refs(payload)
    )
