"""Ticket attachment storage and image handling.

Files live on disk under ATTACHMENTS_DIR, named by ids this app generates, so an uploaded
filename never reaches the filesystem. path/save/delete are the only code that touches the
directory; swap them to move storage to S3.
"""

import io
import re
import shutil
import threading
from collections.abc import Iterable
from pathlib import Path

from fastapi import HTTPException, status
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from sqlalchemy import func
from sqlmodel import Session, select

from app.config import settings
from app.models import AttachmentSource, Ticket, TicketAttachment

ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
PROJECT_QUOTA_BYTES = 1024 * 1024 * 1024
IMAGE_MAX_EDGE = 2000
# A decode at the cap peaks near 150 MB (RGBA PNG) on a 512 MB nano, so decodes run one at a
# time: uploads and the Gmail poller share this process.
IMAGE_MAX_PIXELS = 25_000_000
_DECODE_LOCK = threading.Lock()
FILENAME_MAX_LENGTH = 255
CONTENT_TYPE_MAX_LENGTH = 100
OCTET_STREAM = "application/octet-stream"


def path(project_id: str, attachment_id: str) -> Path:
    return Path(settings.attachments_dir) / project_id / attachment_id


def save(project_id: str, attachment_id: str, data: bytes) -> None:
    target = path(project_id, attachment_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def delete(project_id: str, attachment_id: str) -> None:
    path(project_id, attachment_id).unlink(missing_ok=True)


def delete_project_files(project_id: str) -> None:
    shutil.rmtree(Path(settings.attachments_dir) / project_id, ignore_errors=True)


def clean_filename(filename: str | None) -> str:
    name = re.split(r"[\\/]", filename or "")[-1]
    name = "".join(char if char.isprintable() else " " for char in name)
    return " ".join(name.split())[:FILENAME_MAX_LENGTH] or "file"


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _reencode(data: bytes) -> bytes | None:
    """Downscale to IMAGE_MAX_EDGE and drop metadata. None means keep the original bytes."""
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > IMAGE_MAX_PIXELS:
            raise ValueError("image is too large to decode safely")
        if getattr(image, "is_animated", False):
            return None  # resizing keeps only the first frame
        image_format = image.format
        icc_profile = image.info.get("icc_profile")  # keeps wide-gamut phone photos' colors
        image.draft(image.mode, (IMAGE_MAX_EDGE, IMAGE_MAX_EDGE))  # JPEG: decode at reduced scale
        if image.mode == "P" and max(image.size) > IMAGE_MAX_EDGE:
            image = image.convert("RGBA")  # palette images resize with nearest-neighbour
        # Shrink before rotating so only the small copy is duplicated; reducing_gap=None skips
        # Pillow's full-size intermediate. The box is square, so rotation cannot change the fit.
        image.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE), reducing_gap=None)
        image = ImageOps.exif_transpose(image)
        options = {"optimize": True} if image_format == "PNG" else {"quality": 85}
        out = io.BytesIO()
        image.save(out, image_format, icc_profile=icc_profile, **options)  # no exif= drops EXIF
        return out.getvalue()


def prepare(data: bytes, declared_type: str | None) -> tuple[bytes, str, bool]:
    """Return (bytes to store, content type, is_image) for one incoming file."""
    image_type = sniff_image(data)
    if image_type is None:
        return data, (declared_type or OCTET_STREAM)[:CONTENT_TYPE_MAX_LENGTH], False
    if image_type == "image/gif":
        # Stored as-is (resizing drops the animation), so the pixel cap is the only guard
        # between a tiny file and every viewer's browser decoding a huge canvas.
        try:
            with Image.open(io.BytesIO(data)) as image:
                too_large = image.width * image.height > IMAGE_MAX_PIXELS
        except Exception:
            too_large = True
        return (data, OCTET_STREAM, False) if too_large else (data, image_type, True)
    try:
        with _DECODE_LOCK:
            stored = _reencode(data)
    except Exception:  # corrupt, truncated, or over the pixel cap: keep it as a download
        return data, OCTET_STREAM, False
    return (data if stored is None else stored), image_type, True


def store(
    *,
    project_id: str,
    uploaded_by: str,
    filename: str | None,
    declared_type: str | None,
    data: bytes,
    source: AttachmentSource,
) -> TicketAttachment:
    """Write one file and return its row, unstaged and without a ticket_id: callers stage it,
    and call discard() if the transaction that would record it fails."""
    stored, content_type, is_image = prepare(data, declared_type)
    row = TicketAttachment(
        project_id=project_id,
        filename=clean_filename(filename),
        content_type=content_type,
        size_bytes=len(stored),
        is_image=is_image,
        source=source,
        uploaded_by=uploaded_by,
    )
    save(project_id, row.id, stored)
    return row


def discard(rows: Iterable[TicketAttachment]) -> None:
    for row in rows:
        delete(row.project_id, row.id)


def project_usage(session: Session, project_id: str) -> int:
    return session.exec(
        select(func.coalesce(func.sum(TicketAttachment.size_bytes), 0)).where(
            TicketAttachment.project_id == project_id
        )
    ).one()


def attach_uploads(
    session: Session,
    ticket: Ticket,
    uploader_id: str,
    files: Iterable[tuple[str | None, str | None, bytes]],
) -> list[TicketAttachment]:
    """Stage every upload or none. The caller commits; on 413 nothing is left on disk."""
    rows: list[TicketAttachment] = []
    used = project_usage(session, ticket.project_id)
    try:
        for filename, declared_type, data in files:
            if len(data) > ATTACHMENT_MAX_BYTES:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    f"{clean_filename(filename)} is larger than 10 MB",
                )
            row = store(
                project_id=ticket.project_id,
                uploaded_by=uploader_id,
                filename=filename,
                declared_type=declared_type,
                data=data,
                source=AttachmentSource.UPLOAD,
            )
            rows.append(row)
            used += row.size_bytes
            if used > PROJECT_QUOTA_BYTES:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "This project's 1 GB of attachment storage is full",
                )
            row.ticket_id = ticket.id
            session.add(row)
    except BaseException:
        discard(rows)
        raise
    return rows


def file_response(row: TicketAttachment) -> FileResponse:
    target = path(row.project_id, row.id)
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    response = FileResponse(
        target,
        media_type=row.content_type if row.is_image else OCTET_STREAM,
        filename=row.filename,
        content_disposition_type="inline" if row.is_image else "attachment",
    )
    # sandbox: even a file the browser renders runs no script in this origin.
    response.headers["Content-Security-Policy"] = "sandbox; frame-ancestors 'none'"
    return response
