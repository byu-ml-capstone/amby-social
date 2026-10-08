# Ticket Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach files to tickets by drop, paste, or picker; import Gmail attachments with the ticket; let MCP clients view attached images.

**Architecture:** A `ticket_attachment` table plus files on disk under `ATTACHMENTS_DIR/{project_id}/{attachment_id}`. `app/attachments.py` owns storage, image detection and downscaling (Pillow), staging rows, and the file response. Web routes return an htmx-style partial that vanilla JS swaps in. Gmail sync downloads real attachments and hands the rows to `create_ticket`, which commits them with the ticket. The API adds a bytes route that MCP's `get_attachment` tool uses.

**Tech Stack:** FastAPI, SQLModel/SQLite, Alembic, Jinja2, vanilla JS (`app/static/app.js`), Pillow (new), MCP SDK 2.2 (`mcp.server.mcpserver.Image`), httpx.

**Spec:** `docs/superpowers/specs/2026-09-29-ticket-attachments-design.md`

## Global Constraints

- 10 MB per incoming file (`ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024`), checked before processing; 1 GB stored bytes per project (`PROJECT_QUOTA_BYTES`), checked with `SUM(size_bytes)`.
- Images = PNG, JPEG, GIF, WebP by magic bytes only. SVG and everything else are plain files.
- PNG/JPEG/WebP long edge > 2000 px → 2000 px, same format, JPEG/WebP quality 85, PNG `optimize=True`; EXIF orientation applied then EXIF dropped. GIF and animated images stored unchanged. Over 25 megapixels → stored unchanged as a plain file.
- MCP image long edge ≤ 1568 px.
- Served files: `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; frame-ancestors 'none'`; images `inline` with detected type; everything else `attachment` + `application/octet-stream`.
- Delete = uploader or project OWNER. Gmail files count as uploaded by the user who connected Gmail.
- Enum values uppercase (`UPLOAD` / `GMAIL`).
- Section placement: between the Details editor and `ticket-detail-grid`.
- Never commit `.env`. The user has uncommitted WIP in `app/static/app.css`, `app/templates/board.html`, `app/templates/dashboard.html`, `tests/test_web_board.py`, `tests/test_web_dashboard.py`: never stage, reformat, or commit those hunks (Task 4 has the safe-staging steps for `app.css`).
- Run tests with `uv run pytest`. Run `uv run ruff format <files>` then `uv run ruff check <files>` on files you touch.

## Spec deviations (decided while reading the code)

- **`create_ticket` gains `attachment_rows`.** SQLAlchemy does not order inserts by foreign key without a `relationship()` (verified: child-before-parent insert fails with `FOREIGN KEY constraint failed` under `PRAGMA foreign_keys=ON`), and `create_ticket` both autoflushes and commits. The Gmail path therefore passes unstaged rows in; `create_ticket` flushes the ticket, points the rows at it, and commits them in the same transaction the spec asks for.
- **`get_ticket` returns `TicketDetailOut`** (`TicketOut` + `attachments`). Keeping `TicketOut` unchanged avoids an N+1 query in `list_tickets`, whose MCP tool drops extra fields anyway.
- **Upload and delete are JavaScript-driven.** Both routes always return the section partial, with no redirect branch. Drop and paste need JS in any case, and a no-JS form would have to nest inside the ticket form, which HTML does not allow.
- **The security-header middleware uses `setdefault` for CSP**, so a file response's `sandbox` policy is not overwritten.

## Review Focus

1. Copying text from Word/Excel also puts a picture of that text on the clipboard. Pasting it into the description or a comment must still paste text, not upload a picture (JS guard; checked in Task 4's browser pass).
2. iPhone HEIC photos and other unsupported images are stored as downloads, with no crash and no inline serving (Task 2 test).
3. A small file that decodes to a huge canvas (decompression bomb) is stored as a download without being decoded (Task 2 test, cap monkeypatched).
4. Filenames containing path parts, CR/LF, quotes, or Korean text never reach the filesystem and stay safe in `Content-Disposition` (Task 2 `clean_filename` test, Task 3 Korean header test).
5. Deleting a ticket that belongs to closed sprint history (409) keeps its attachment rows and files. The guard runs before any delete (Task 3 test).

---

### Task 1: Data model, migration, setting

**Files:**
- Modify: `app/models.py` (enum after `GmailConnectionStatus`; table after `GmailConnection`)
- Modify: `app/config.py` (after `token_encryption_key`)
- Create: `alembic/versions/b7e2d4f1a9c3_add_ticket_attachments.py`
- Modify: `tests/conftest.py` (new autouse fixture)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `AttachmentSource(StrEnum)` with `UPLOAD`, `GMAIL`. `TicketAttachment` with fields `id, ticket_id, project_id, filename, content_type, size_bytes, is_image, source, uploaded_by, created_at`. `settings.attachments_dir: str`. Tests get `settings.attachments_dir` pointing at `tmp_path / "attachments"`.

- [ ] **Step 1: Write the failing migration test** — append to `tests/test_migrations.py`:

```python
def test_attachment_migration_round_trip(tmp_path):
    db = tmp_path / "attachments.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)
    inspector = inspect(make_engine(f"sqlite:///{db}"))
    assert "ticket_attachment" in inspector.get_table_names()
    assert {index["name"] for index in inspector.get_indexes("ticket_attachment")} == {
        "ix_ticket_attachment_ticket_id",
        "ix_ticket_attachment_project_id",
    }
    subprocess.run(["uv", "run", "alembic", "downgrade", "a3f7c9e2b815"], check=True, env=env)
    assert "ticket_attachment" not in inspect(make_engine(f"sqlite:///{db}")).get_table_names()
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_migrations.py::test_attachment_migration_round_trip -q`
Expected: FAIL, `assert 'ticket_attachment' in [...]`.

- [ ] **Step 3: Add the model, setting, migration, and test isolation**

`app/models.py`, after `class GmailConnectionStatus`:

```python
class AttachmentSource(StrEnum):
    UPLOAD = "UPLOAD"
    GMAIL = "GMAIL"
```

`app/models.py`, after `class GmailConnection` (end of file):

```python
class TicketAttachment(SQLModel, table=True):
    __tablename__ = "ticket_attachment"

    id: str = Field(default_factory=new_id, primary_key=True)
    ticket_id: str = Field(foreign_key="ticket.id", index=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    filename: str = Field(max_length=255)  # display only; the file on disk is named by id
    content_type: str = Field(max_length=100)
    size_bytes: int
    is_image: bool = False
    source: AttachmentSource = Field(default=AttachmentSource.UPLOAD)
    uploaded_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)
```

`app/config.py`, after `token_encryption_key: str | None = None`:

```python
    attachments_dir: str = "data/attachments"
```

`alembic/versions/b7e2d4f1a9c3_add_ticket_attachments.py`:

```python
"""add ticket attachments

Revision ID: b7e2d4f1a9c3
Revises: a3f7c9e2b815
"""

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "b7e2d4f1a9c3"
down_revision = "a3f7c9e2b815"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_attachment",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ticket_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("filename", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("content_type", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("is_image", sa.Boolean(), nullable=False),
        sa.Column("source", sa.Enum("UPLOAD", "GMAIL", name="attachmentsource"), nullable=False),
        sa.Column("uploaded_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["ticket.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["uploaded_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ticket_attachment_ticket_id", "ticket_attachment", ["ticket_id"], unique=False
    )
    op.create_index(
        "ix_ticket_attachment_project_id", "ticket_attachment", ["project_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_attachment_project_id", table_name="ticket_attachment")
    op.drop_index("ix_ticket_attachment_ticket_id", table_name="ticket_attachment")
    op.drop_table("ticket_attachment")
```

`tests/conftest.py`, after the `isolate_security_state` fixture:

```python
@pytest.fixture(autouse=True)
def isolate_attachments(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "attachments_dir", str(tmp_path / "attachments"))
```

- [ ] **Step 4: Run the migration tests**

Run: `uv run pytest tests/test_migrations.py -q`
Expected: all pass, including `test_migration_produces_the_same_tables_as_the_models`, which now covers the new table's columns.

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/config.py alembic/versions/b7e2d4f1a9c3_add_ticket_attachments.py tests/conftest.py tests/test_migrations.py
git commit -m "feat: add ticket attachment table and storage setting"
```

---

### Task 2: Storage and image handling (`app/attachments.py`)

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add pillow`)
- Create: `app/attachments.py`
- Create: `tests/images.py`
- Test: `tests/test_attachments.py`

**Interfaces:**
- Consumes: `settings.attachments_dir` (Task 1).
- Produces, in `app.attachments`:
  - Constants: `ATTACHMENT_MAX_BYTES`, `PROJECT_QUOTA_BYTES`, `IMAGE_MAX_EDGE = 2000`, `IMAGE_MAX_PIXELS = 25_000_000`, `OCTET_STREAM`.
  - `path(project_id, attachment_id) -> Path`, `save(project_id, attachment_id, data: bytes) -> None`, `delete(project_id, attachment_id) -> None`, `delete_project_files(project_id) -> None`.
  - `clean_filename(filename: str | None) -> str`, `sniff_image(data: bytes) -> str | None`, `prepare(data: bytes, declared_type: str | None) -> tuple[bytes, str, bool]` returning (stored bytes, content type, is_image).
- Produces `tests.images.image_bytes(size=(40, 30), fmt="PNG", **save) -> bytes`.

- [ ] **Step 1: Add Pillow**

Run: `uv add pillow`
Expected: `pyproject.toml` dependencies gain `"pillow>=…"`; `uv.lock` updated.

- [ ] **Step 2: Write the failing tests**

`tests/images.py`:

```python
import io

from PIL import Image


def image_bytes(size=(40, 30), fmt="PNG", **save) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "red").save(out, fmt, **save)
    return out.getvalue()
```

`tests/test_attachments.py`:

```python
import io

import pytest
from PIL import Image

from app import attachments
from tests.images import image_bytes


def _open(data):
    return Image.open(io.BytesIO(data))


def test_storage_round_trip_is_keyed_by_ids():
    attachments.save("project-1", "att-1", b"hello")
    assert attachments.path("project-1", "att-1").read_bytes() == b"hello"
    attachments.delete("project-1", "att-1")
    assert not attachments.path("project-1", "att-1").exists()
    attachments.delete("project-1", "att-1")  # already gone is fine


def test_project_files_are_removed_together():
    attachments.save("project-1", "att-1", b"a")
    attachments.save("project-1", "att-2", b"b")
    attachments.delete_project_files("project-1")
    assert not attachments.path("project-1", "att-1").parent.exists()


def test_uploaded_filename_is_display_only():
    assert attachments.clean_filename("../../etc/passwd") == "passwd"
    assert attachments.clean_filename("C:\\Users\\me\\brief.pdf") == "brief.pdf"
    assert attachments.clean_filename('evil\r\n"name".pdf') == 'evil "name".pdf'
    assert attachments.clean_filename("") == "file"
    assert attachments.clean_filename(None) == "file"
    assert len(attachments.clean_filename("a" * 300 + ".pdf")) == 255


@pytest.mark.parametrize(
    ("fmt", "content_type"),
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("GIF", "image/gif"), ("WEBP", "image/webp")],
)
def test_real_images_are_detected_by_their_bytes(fmt, content_type):
    assert attachments.sniff_image(image_bytes(fmt=fmt)) == content_type


def test_markup_is_never_an_image_whatever_its_declared_type():
    html = b"<html><script>alert(1)</script></html>"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert attachments.prepare(html, "image/png") == (html, "image/png", False)
    assert attachments.prepare(svg, "image/svg+xml") == (svg, "image/svg+xml", False)
    assert attachments.prepare(b"%PDF-1.4", None) == (b"%PDF-1.4", "application/octet-stream", False)


def test_unsupported_image_format_is_a_download():
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16
    assert attachments.prepare(heic, "image/heic") == (heic, "image/heic", False)


def test_large_image_is_downscaled_in_its_own_format_without_exif():
    exif = Image.Exif()
    exif[0x010F] = "Camera maker"
    data = image_bytes((3000, 1500), "JPEG", exif=exif.tobytes())
    stored, content_type, is_image = attachments.prepare(data, "image/jpeg")
    with _open(stored) as image:
        assert (image.format, image.size) == ("JPEG", (2000, 1000))
        assert not image.getexif()
    assert (content_type, is_image) == ("image/jpeg", True)


def test_exif_orientation_is_applied_before_exif_is_dropped():
    exif = Image.Exif()
    exif[0x0112] = 6  # display rotated 90° clockwise
    stored, _, _ = attachments.prepare(image_bytes((300, 100), "JPEG", exif=exif.tobytes()), None)
    with _open(stored) as image:
        assert image.size == (100, 300)


def test_small_png_keeps_its_size():
    stored, content_type, is_image = attachments.prepare(image_bytes((640, 480)), None)
    with _open(stored) as image:
        assert (image.format, image.size) == ("PNG", (640, 480))
    assert (content_type, is_image) == ("image/png", True)


def test_gif_is_stored_unchanged():
    data = image_bytes((3000, 100), "GIF")
    assert attachments.prepare(data, "image/gif") == (data, "image/gif", True)


def test_animated_webp_is_stored_unchanged():
    frames = [Image.new("RGB", (3000, 10), color) for color in ("red", "blue")]
    out = io.BytesIO()
    frames[0].save(out, "WEBP", save_all=True, append_images=frames[1:])
    data = out.getvalue()
    assert attachments.prepare(data, None) == (data, "image/webp", True)


def test_image_over_the_pixel_cap_is_kept_as_a_download(monkeypatch):
    monkeypatch.setattr(attachments, "IMAGE_MAX_PIXELS", 100)
    data = image_bytes((20, 20))
    assert attachments.prepare(data, "image/png") == (data, "application/octet-stream", False)


def test_corrupt_image_is_kept_as_a_download():
    data = image_bytes()[:40]
    assert attachments.prepare(data, "image/png") == (data, "application/octet-stream", False)
```

- [ ] **Step 3: Run them**

Run: `uv run pytest tests/test_attachments.py -q`
Expected: FAIL at collection, `ImportError: cannot import name 'attachments' from 'app'`.

- [ ] **Step 4: Implement `app/attachments.py`**

```python
"""Ticket attachment storage and image handling.

Files live on disk under ATTACHMENTS_DIR, named by ids this app generates, so an uploaded
filename never reaches the filesystem. path/save/delete are the only code that touches the
directory; swap them to move storage to S3.
"""

import io
import re
import shutil
from pathlib import Path

from PIL import Image, ImageOps

from app.config import settings

ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
PROJECT_QUOTA_BYTES = 1024 * 1024 * 1024
IMAGE_MAX_EDGE = 2000
# ponytail: a decode at the cap is ~100 MB of RGBA on a 512 MB nano, and two concurrent
# uploads double it. Put a semaphore around prepare() if uploads become concurrent.
IMAGE_MAX_PIXELS = 25_000_000
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
        image = ImageOps.exif_transpose(image)
        if image.mode == "P" and max(image.size) > IMAGE_MAX_EDGE:
            image = image.convert("RGBA")  # palette images resize with nearest-neighbour
        image.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE))
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
        return data, image_type, True  # resizing would drop the animation
    try:
        stored = _reencode(data)
    except Exception:  # corrupt, truncated, or over the pixel cap: keep it as a download
        return data, OCTET_STREAM, False
    return (data if stored is None else stored), image_type, True
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_attachments.py -q`
Expected: all pass. If Pillow emits a `DeprecationWarning` (the suite treats those as errors), fix the call rather than filtering it.

- [ ] **Step 6: Commit**

```bash
uv run ruff format app/attachments.py tests/test_attachments.py tests/images.py && uv run ruff check app/attachments.py tests/test_attachments.py tests/images.py
git add pyproject.toml uv.lock app/attachments.py tests/images.py tests/test_attachments.py
git commit -m "feat: store attachments on disk and downscale images"
```

---

### Task 3: Upload, serve, delete routes and the Attachments section

**Files:**
- Modify: `app/attachments.py` (staging, quota, file response)
- Modify: `app/main.py:68` (CSP `setdefault`)
- Modify: `app/services.py` (`delete_ticket_record`, `delete_project`, `create_ticket`)
- Modify: `app/routers/web.py` (helpers, `_ticket_detail` context, three routes)
- Create: `app/templates/partials/ticket_attachments.html`
- Modify: `app/templates/partials/ticket_detail.html:65` (include after the description editor)
- Test: `tests/test_web_ticket_attachments.py`

**Interfaces:**
- Consumes: Task 2's `prepare`, `save`, `delete`, `delete_project_files`, `clean_filename`, `path`, and constants.
- Produces, in `app.attachments`:
  - `store(*, project_id, uploaded_by, filename, declared_type, data, source) -> TicketAttachment`: writes the file and returns an **unstaged** row with `ticket_id` unset.
  - `attach_uploads(session, ticket, uploader_id, files: Iterable[tuple[str | None, str | None, bytes]]) -> list[TicketAttachment]`: stages rows, commits nothing, and raises 413 `HTTPException` after removing its own files.
  - `discard(rows) -> None`, `project_usage(session, project_id) -> int`, `file_response(row) -> FileResponse`.
- Produces `create_ticket(..., attachment_rows: Sequence[TicketAttachment] = ())`.
- Produces the routes `POST /projects/{slug}/tickets/{n}/attachments` (form field `files`), `GET /projects/{slug}/attachments/{id}`, and `POST /projects/{slug}/tickets/{n}/attachments/{id}/delete`.
- Produces the partial's DOM contract, which JS in Task 4 relies on:
  - `section#ticket-attachments[data-ticket-attachments][data-upload-url][data-csrf][data-max-bytes]`
  - `[data-attachment-pick]` button and `input[type=file][data-attachment-input]`
  - `[data-attachment-status]` and `[data-attachment-delete="<url>"]`

- [ ] **Step 1: Write the failing tests** — `tests/test_web_ticket_attachments.py`:

```python
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from sqlmodel import Session, select

from app import attachments
from app.attachments import ATTACHMENT_MAX_BYTES
from app.auth import make_csrf_token
from app.config import settings
from app.models import (
    AttachmentSource,
    Project,
    Sprint,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    TicketAttachment,
)
from app.services import delete_project
from tests.images import image_bytes


@pytest.fixture
def world(make_user, make_project, add_member, engine):
    owner = make_user(email="ada@example.com", name="Ada")
    member = make_user(email="bob@example.com", name="Bob")
    teammate = make_user(email="cy@example.com", name="Cy")
    outsider = make_user(email="eve@example.com", name="Eve")
    project = make_project(owner)
    add_member(project, member)
    add_member(project, teammate)
    with Session(engine) as session:
        ticket = Ticket(
            ticket_number=1, project_id=project.id, title="Campaign review", creator_id=owner.id
        )
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
    return SimpleNamespace(
        owner=owner,
        member=member,
        teammate=teammate,
        outsider=outsider,
        project=project,
        ticket=ticket,
    )


def _upload(client, world, user, *files):
    return client.post(
        f"/projects/{world.project.slug}/tickets/1/attachments",
        data={"_csrf": make_csrf_token(user.id)},
        files=[("files", file) for file in files],
        headers={"HX-Request": "true"},
    )


def _delete(client, world, user, attachment_id):
    return client.post(
        f"/projects/{world.project.slug}/tickets/1/attachments/{attachment_id}/delete",
        data={"_csrf": make_csrf_token(user.id)},
        headers={"HX-Request": "true"},
    )


def _rows(engine):
    with Session(engine) as session:
        return {row.filename: row for row in session.exec(select(TicketAttachment)).all()}


def _stored_files():
    root = Path(settings.attachments_dir)
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _detail(client, world):
    return client.get(f"/projects/{world.project.slug}/tickets/1", headers={"HX-Request": "true"})


def test_member_uploads_several_files_at_once(client, world, login_as, engine):
    login_as(world.member.email)
    response = _upload(
        client,
        world,
        world.member,
        ("shot.png", image_bytes(), "image/png"),
        ("brief.pdf", b"%PDF-1.4 brief", "application/pdf"),
    )
    assert response.status_code == 200, response.text
    assert 'id="ticket-attachments"' in response.text
    assert "shot.png" in response.text and "brief.pdf" in response.text
    rows = _rows(engine)
    assert (rows["shot.png"].is_image, rows["shot.png"].content_type) == (True, "image/png")
    assert (rows["brief.pdf"].is_image, rows["brief.pdf"].content_type) == (
        False,
        "application/pdf",
    )
    assert {row.source for row in rows.values()} == {AttachmentSource.UPLOAD}
    assert {row.uploaded_by for row in rows.values()} == {world.member.id}
    assert {row.ticket_id for row in rows.values()} == {world.ticket.id}
    assert len(_stored_files()) == 2


def test_upload_over_10_mb_saves_nothing_and_names_the_file(client, world, login_as, engine):
    login_as(world.member.email)
    response = _upload(
        client,
        world,
        world.member,
        ("shot.png", image_bytes(), "image/png"),
        ("huge.zip", b"0" * (ATTACHMENT_MAX_BYTES + 1), "application/zip"),
    )
    assert response.status_code == 413
    assert 'id="ticket-attachments"' in response.text
    assert "huge.zip is larger than 10 MB" in response.text
    assert _rows(engine) == {} and _stored_files() == []


def test_full_project_storage_rejects_the_upload(client, world, login_as, engine, monkeypatch):
    monkeypatch.setattr(attachments, "PROJECT_QUOTA_BYTES", 10)
    login_as(world.member.email)
    response = _upload(client, world, world.member, ("notes.txt", b"more than ten", "text/plain"))
    assert response.status_code == 413
    assert "attachment storage is full" in response.text
    assert _rows(engine) == {} and _stored_files() == []


def test_non_member_cannot_upload(client, world, login_as, engine):
    login_as(world.outsider.email)
    response = _upload(client, world, world.outsider, ("notes.txt", b"hi", "text/plain"))
    assert response.status_code == 403
    assert _rows(engine) == {}


def test_image_is_served_inline_with_its_detected_type(client, world, login_as, engine):
    login_as(world.member.email)
    _upload(client, world, world.member, ("shot.png", image_bytes(), "application/octet-stream"))
    row = _rows(engine)["shot.png"]
    response = client.get(f"/projects/{world.project.slug}/attachments/{row.id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == 'inline; filename="shot.png"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox; frame-ancestors 'none'"


def test_markup_disguised_as_an_image_downloads_as_octet_stream(client, world, login_as, engine):
    login_as(world.member.email)
    _upload(client, world, world.member, ("cat.png", b"<script>alert(1)</script>", "image/png"))
    row = _rows(engine)["cat.png"]
    assert row.is_image is False
    response = client.get(f"/projects/{world.project.slug}/attachments/{row.id}")
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == 'attachment; filename="cat.png"'


def test_korean_filename_survives_the_download_header(client, world, login_as, engine):
    login_as(world.member.email)
    _upload(client, world, world.member, ("브리프.pdf", b"%PDF-1.4", "application/pdf"))
    row = _rows(engine)["브리프.pdf"]
    response = client.get(f"/projects/{world.project.slug}/attachments/{row.id}")
    assert response.headers["content-disposition"] == (
        f"attachment; filename*=utf-8''{quote('브리프.pdf')}"
    )


def test_attachment_is_only_served_to_its_own_projects_members(
    client, world, login_as, engine, make_project
):
    other = make_project(world.owner, "Other Project")
    login_as(world.member.email)
    _upload(client, world, world.member, ("notes.txt", b"hi", "text/plain"))
    row = _rows(engine)["notes.txt"]
    login_as(world.owner.email)
    assert client.get(f"/projects/{other.slug}/attachments/{row.id}").status_code == 404
    login_as(world.outsider.email)
    assert client.get(f"/projects/{world.project.slug}/attachments/{row.id}").status_code == 404
    client.cookies.clear()
    assert client.get(f"/projects/{world.project.slug}/attachments/{row.id}").status_code == 401


def test_uploader_and_owner_can_delete_but_teammates_cannot(client, world, login_as, engine):
    login_as(world.member.email)
    _upload(client, world, world.member, ("a.txt", b"a", "text/plain"), ("b.txt", b"b", "text/plain"))
    rows = _rows(engine)
    login_as(world.teammate.email)
    assert _delete(client, world, world.teammate, rows["a.txt"].id).status_code == 403
    assert len(_stored_files()) == 2
    login_as(world.member.email)
    response = _delete(client, world, world.member, rows["a.txt"].id)
    assert response.status_code == 200 and 'id="ticket-attachments"' in response.text
    login_as(world.owner.email)
    assert _delete(client, world, world.owner, rows["b.txt"].id).status_code == 200
    assert _rows(engine) == {} and _stored_files() == []


def test_deleting_the_ticket_removes_its_attachments(client, world, login_as, engine):
    login_as(world.owner.email)
    _upload(client, world, world.owner, ("notes.txt", b"hi", "text/plain"))
    response = client.post(
        f"/projects/{world.project.slug}/tickets/1/delete",
        data={"_csrf": make_csrf_token(world.owner.id)},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 204
    assert _rows(engine) == {} and _stored_files() == []


def test_ticket_in_closed_sprint_history_keeps_its_attachments(client, world, login_as, engine):
    login_as(world.owner.email)
    _upload(client, world, world.owner, ("notes.txt", b"hi", "text/plain"))
    with Session(engine) as session:
        sprint = Sprint(
            project_id=world.project.id,
            name="Closed",
            goal="Done",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 14),
            status=SprintStatus.CLOSED,
        )
        session.add(sprint)
        session.flush()
        session.add(SprintTicketHistory(sprint_id=sprint.id, ticket_id=world.ticket.id))
        session.commit()
    response = client.post(
        f"/projects/{world.project.slug}/tickets/1/delete",
        data={"_csrf": make_csrf_token(world.owner.id)},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 409
    assert list(_rows(engine)) == ["notes.txt"] and len(_stored_files()) == 1


def test_deleting_the_project_removes_its_attachments(client, world, login_as, engine):
    login_as(world.owner.email)
    _upload(client, world, world.owner, ("notes.txt", b"hi", "text/plain"))
    with Session(engine) as session:
        delete_project(session, session.get(Project, world.project.id), world.project.slug)
    assert _rows(engine) == {} and _stored_files() == []


def test_ticket_detail_shows_attachments_between_details_and_fields(
    client, world, login_as, engine
):
    login_as(world.member.email)
    _upload(
        client,
        world,
        world.member,
        ("shot.png", image_bytes(), "image/png"),
        ("brief.pdf", b"%PDF-1.4", "application/pdf"),
    )
    image = _rows(engine)["shot.png"]
    text = _detail(client, world).text
    assert (
        text.index("data-description-editor")
        < text.index('id="ticket-attachments"')
        < text.index('class="ticket-detail-grid"')
    )
    image_url = f"/projects/{world.project.slug}/attachments/{image.id}"
    assert f'href="{image_url}" target="_blank" rel="noopener"' in text
    assert f'src="{image_url}"' in text and 'loading="lazy"' in text
    assert "brief.pdf" in text
    assert "Drop files here" not in text


def test_empty_ticket_invites_a_drop_or_paste(client, world, login_as):
    login_as(world.member.email)
    assert "Drop files here or paste a screenshot." in _detail(client, world).text


def test_delete_buttons_show_only_to_the_uploader_and_owners(client, world, login_as):
    login_as(world.member.email)
    _upload(client, world, world.member, ("notes.txt", b"hi", "text/plain"))
    assert "data-attachment-delete=" in _detail(client, world).text
    login_as(world.teammate.email)
    assert "data-attachment-delete=" not in _detail(client, world).text
    login_as(world.owner.email)
    assert "data-attachment-delete=" in _detail(client, world).text


def test_ordinary_pages_keep_the_default_security_policy(client, world, login_as):
    login_as(world.member.email)
    assert _detail(client, world).headers["content-security-policy"] == "frame-ancestors 'none'"
```

Check the `Sprint` required fields against `app/models.py:208` before running. If `start_date`/`end_date` need `date` objects or other columns are required, adjust the test fixture to fit the model, not the other way round.

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_web_ticket_attachments.py -q`
Expected: FAIL, mostly 404/405 on the attachment routes and missing `id="ticket-attachments"`.

- [ ] **Step 3: Add staging, quota, and the file response to `app/attachments.py`**

Add imports:

```python
from collections.abc import Iterable

from fastapi import HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlmodel import Session, select

from app.models import AttachmentSource, Ticket, TicketAttachment
```

Append:

```python
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
```

If `status.HTTP_413_CONTENT_TOO_LARGE` does not exist in the installed Starlette, use `status.HTTP_413_REQUEST_ENTITY_TOO_LARGE`.

- [ ] **Step 4: Keep a response's own CSP** — `app/main.py`, in `_set_security_headers`, replace the CSP line:

```python
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
```

- [ ] **Step 5: Cascade deletes and accept rows in `create_ticket`** — `app/services.py`:

Imports: add `from collections.abc import Sequence`, `from app import attachments`, and `TicketAttachment` to the `app.models` import list.

`delete_ticket_record` becomes:

```python
def delete_ticket_record(session: Session, ticket: Ticket) -> None:
    if session.exec(
        select(SprintTicketHistory).where(SprintTicketHistory.ticket_id == ticket.id)
    ).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket belongs to closed sprint history")
    project_id = ticket.project_id
    attachment_ids = session.exec(
        select(TicketAttachment.id).where(TicketAttachment.ticket_id == ticket.id)
    ).all()
    session.execute(delete(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id))
    session.execute(delete(TicketGitLink).where(TicketGitLink.ticket_id == ticket.id))
    session.execute(delete(TicketComment).where(TicketComment.ticket_id == ticket.id))
    session.delete(ticket)
    session.commit()
    for attachment_id in attachment_ids:  # after commit: a failure leaves an orphan file only
        attachments.delete(project_id, attachment_id)
```

In `delete_project`, add this line just before `session.execute(delete(TicketComment)…`:

```python
    session.execute(delete(TicketAttachment).where(TicketAttachment.project_id == project.id))
```

Add `project_id = project.id` as the first line after the confirm check, and after the final `session.commit()` add:

```python
    attachments.delete_project_files(project_id)
```

In `create_ticket`, add the keyword parameter `attachment_rows: Sequence[TicketAttachment] = (),` after `tasks`, and replace `session.add(ticket)` with:

```python
    session.add(ticket)
    if attachment_rows:
        # SQLite checks foreign keys per insert and the ORM does not order these tables.
        session.flush()
        for row in attachment_rows:
            row.ticket_id = ticket.id
        session.add_all(attachment_rows)
```

- [ ] **Step 6: Routes and context** — `app/routers/web.py`:

Imports: add `File` and `UploadFile` to the `fastapi` import, add `from app import attachments`, and add `TicketAttachment` to the `app.models` import. Add a module constant next to the other hoisted `Form` defaults:

```python
_FILES_FORM = File(...)
```

Helpers, placed after `_render_ticket_comments`:

```python
def _attachment_context(
    session: Session, project: Project, ticket: Ticket, viewer: User, error: str | None = None
) -> dict:
    role = session.exec(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == viewer.id
        )
    ).one()
    rows = session.exec(
        select(TicketAttachment)
        .where(TicketAttachment.ticket_id == ticket.id)
        .order_by(TicketAttachment.created_at)
    ).all()
    items = [
        {
            "attachment": row,
            "url": f"/projects/{project.slug}/attachments/{row.id}",
            "delete_url": (
                f"/projects/{project.slug}/tickets/{ticket.ticket_number}"
                f"/attachments/{row.id}/delete"
            ),
            "can_delete": row.uploaded_by == viewer.id or role == Role.OWNER,
        }
        for row in rows
    ]
    return {
        "attachment_images": [item for item in items if item["attachment"].is_image],
        "attachment_files": [item for item in items if not item["attachment"].is_image],
        "attachment_error": error,
        "attachment_max_bytes": attachments.ATTACHMENT_MAX_BYTES,
    }


def _render_ticket_attachments(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    ticket: Ticket,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    return render(
        request,
        "partials/ticket_attachments.html",
        {
            "user": user,
            "project": project,
            "ticket": ticket,
            **_attachment_context(session, project, ticket, user, error),
        },
        status_code=status_code,
    )
```

In `_ticket_detail`, add `**_attachment_context(session, project, ticket, user),` to the context dict (after `"comment_error": comment_error,`).

Routes, placed after `delete_ticket_comment_form`:

```python
@router.post(
    "/projects/{slug}/tickets/{ticket_number}/attachments",
    dependencies=[Depends(verify_csrf)],
)
def upload_ticket_attachments(
    slug: str,
    ticket_number: int,
    request: Request,
    files: list[UploadFile] = _FILES_FORM,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    uploads = (
        (upload.filename, upload.content_type, upload.file.read(attachments.ATTACHMENT_MAX_BYTES + 1))
        for upload in files
    )
    try:
        rows = attachments.attach_uploads(session, ticket, user.id, uploads)
    except HTTPException as exc:
        session.rollback()
        return _render_ticket_attachments(
            request, session, user, project, ticket, error=exc.detail, status_code=exc.status_code
        )
    try:
        session.commit()
    except Exception:
        session.rollback()
        attachments.discard(rows)
        raise
    return _render_ticket_attachments(request, session, user, project, ticket)


@router.get("/projects/{slug}/attachments/{attachment_id}")
def ticket_attachment_file(
    slug: str,
    attachment_id: str,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_reader),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    row = session.get(TicketAttachment, attachment_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return attachments.file_response(row)


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/attachments/{attachment_id}/delete",
    dependencies=[Depends(verify_csrf)],
)
def delete_ticket_attachment(
    slug: str,
    ticket_number: int,
    attachment_id: str,
    request: Request,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, membership = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    row = session.get(TicketAttachment, attachment_id)
    if row is None or row.ticket_id != ticket.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    if row.uploaded_by != user.id and membership.role != Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot delete this attachment")
    session.delete(row)
    session.commit()
    attachments.delete(project.id, attachment_id)
    return _render_ticket_attachments(request, session, user, project, ticket)
```

- [ ] **Step 7: Templates**

`app/templates/partials/ticket_attachments.html`:

```html
<section id="ticket-attachments" class="ticket-attachments" aria-labelledby="ticket-attachments-heading"
         data-ticket-attachments
         data-upload-url="/projects/{{ project.slug }}/tickets/{{ ticket.ticket_number }}/attachments"
         data-csrf="{{ csrf_token }}" data-max-bytes="{{ attachment_max_bytes }}">
  <div class="ticket-description-header">
    <span id="ticket-attachments-heading" class="ticket-description-label">Attachments</span>
    <button type="button" class="app-ghost-button" data-attachment-pick>Add files</button>
    <input type="file" multiple hidden data-attachment-input aria-label="Add files">
  </div>
  {% if attachment_error %}<p class="form-error" role="alert">{{ attachment_error }}</p>{% endif %}
  <p class="ticket-attachments-status" role="status" aria-live="polite" data-attachment-status></p>
  {% if attachment_images %}
  <ul class="ticket-attachment-grid" role="list">
    {% for item in attachment_images %}
    <li class="ticket-attachment-tile">
      <a href="{{ item.url }}" target="_blank" rel="noopener" title="{{ item.attachment.filename }}">
        <img src="{{ item.url }}" alt="{{ item.attachment.filename }}" loading="lazy" width="96" height="96">
      </a>
      {% if item.attachment.source.value == 'GMAIL' %}<span class="ticket-attachment-source">Gmail</span>{% endif %}
      {% if item.can_delete %}<button type="button" class="ticket-attachment-delete" data-attachment-delete="{{ item.delete_url }}" aria-label="Delete {{ item.attachment.filename }}" title="Delete">×</button>{% endif %}
    </li>
    {% endfor %}
  </ul>
  {% endif %}
  {% if attachment_files %}
  <ul class="ticket-attachment-files" role="list">
    {% for item in attachment_files %}
    <li class="ticket-attachment-file">
      <a href="{{ item.url }}" download>{{ item.attachment.filename }}</a>
      <span class="ticket-attachment-meta">{{ item.attachment.size_bytes|filesizeformat }}{% if item.attachment.source.value == 'GMAIL' %} · from Gmail{% endif %}</span>
      {% if item.can_delete %}<button type="button" class="ticket-attachment-delete" data-attachment-delete="{{ item.delete_url }}" aria-label="Delete {{ item.attachment.filename }}" title="Delete">×</button>{% endif %}
    </li>
    {% endfor %}
  </ul>
  {% endif %}
  {% if not attachment_images and not attachment_files %}<p class="ticket-attachments-empty">Drop files here or paste a screenshot.</p>{% endif %}
</section>
```

`app/templates/partials/ticket_detail.html`: after the closing `</div>` of `ticket-description-editor` (line 65) and before `<div class="ticket-detail-grid">`, add:

```html
    {% include "partials/ticket_attachments.html" %}
```

The section sits inside the ticket `<form>`. That is safe: it has no nested `<form>`, its buttons are `type="button"`, and the file input has no `name`, so "Save changes" never submits it.

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/test_web_ticket_attachments.py -q`
Expected: all pass. If the Korean header differs only by the case of `utf-8`, match Starlette's actual output in the assertion and note it. The server is correct either way (RFC 5987 charset is case-insensitive).

- [ ] **Step 9: Full suite**

Run: `uv run pytest -q > .superpowers/sdd/2026-09-29-ticket-attachments/task3.log 2>&1; tail -5 .superpowers/sdd/2026-09-29-ticket-attachments/task3.log`
Expected: all pass. The `setdefault` change must not break `tests/test_security.py`.

- [ ] **Step 10: Commit**

```bash
F=(app/attachments.py app/main.py app/services.py app/routers/web.py tests/test_web_ticket_attachments.py)
uv run ruff format "${F[@]}" && uv run ruff check "${F[@]}"
git add "${F[@]}" app/templates/partials/ticket_attachments.html app/templates/partials/ticket_detail.html
git commit -m "feat: upload, serve, and delete ticket attachments"
```

If `ruff format` reformats pre-existing lines in `app/services.py` or `app/routers/web.py` beyond your edits, revert those hunks before staging. Repo-wide ruff debt is out of scope.

---

### Task 4: Drop, paste, picker, and styling

**Files:**
- Modify: `app/static/app.js` (new IIFE at the end)
- Modify: `app/static/app.css` (after `.ticket-development-empty`, line ~193; the user's WIP hunk is at ~958)
- Verify: Playwright script in the session scratchpad (not committed)

**Interfaces:**
- Consumes: the partial's DOM contract from Task 3.

- [ ] **Step 1: Snapshot `app.css` before editing** (the user's WIP must stay unstaged)

```bash
cp app/static/app.css .superpowers/sdd/2026-09-29-ticket-attachments/app.css.before
```

- [ ] **Step 2: JS** — append to `app/static/app.js`:

```js
(() => {
  const sectionSelector = "[data-ticket-attachments]";

  function setStatus(section, message) {
    section.querySelector("[data-attachment-status]").textContent = message;
  }

  async function post(section, url, body, failure) {
    body.append("_csrf", section.dataset.csrf);
    section.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(url, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: { "HX-Request": "true" },
      });
      // Errors come back as the section too (with its message); anything else is a failure.
      const template = document.createElement("template");
      template.innerHTML = await response.text();
      const next = template.content.querySelector(sectionSelector);
      if (!next) throw new Error();
      section.replaceWith(next);
    } catch {
      section.removeAttribute("aria-busy");
      setStatus(section, failure);
    }
  }

  function upload(section, fileList) {
    const files = [...fileList];
    if (!section || !files.length) return;
    const tooLarge = files.find((file) => file.size > Number(section.dataset.maxBytes));
    if (tooLarge) return setStatus(section, `${tooLarge.name} is larger than 10 MB.`);
    const body = new FormData();
    files.forEach((file) => body.append("files", file, file.name || "pasted-image.png"));
    setStatus(section, "Uploading…");
    post(section, section.dataset.uploadUrl, body, "Upload failed. Try again.");
  }

  // The whole ticket panel is the drop target; the section shows where files land.
  const sectionIn = (target) => target.closest?.("#ticket-detail-panel")?.querySelector(sectionSelector);
  const carriesFiles = (event) => [...(event.dataTransfer?.types || [])].includes("Files");

  document.addEventListener("click", (event) => {
    const pick = event.target.closest("[data-attachment-pick]");
    if (pick) return pick.closest(sectionSelector).querySelector("[data-attachment-input]").click();
    const remove = event.target.closest("[data-attachment-delete]");
    if (remove && window.confirm("Delete this file?")) {
      post(remove.closest(sectionSelector), remove.dataset.attachmentDelete, new FormData(), "Could not delete the file. Try again.");
    }
  });

  document.addEventListener("change", (event) => {
    if (!event.target.matches("[data-attachment-input]")) return;
    upload(event.target.closest(sectionSelector), event.target.files);
    event.target.value = "";
  });

  document.addEventListener("dragover", (event) => {
    const section = carriesFiles(event) && sectionIn(event.target);
    if (!section) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    section.classList.add("is-drag-over");
  });

  document.addEventListener("dragleave", (event) => {
    const panel = event.target.closest?.("#ticket-detail-panel");
    if (panel && !panel.contains(event.relatedTarget)) {
      panel.querySelector(sectionSelector)?.classList.remove("is-drag-over");
    }
  });

  document.addEventListener("drop", (event) => {
    const section = carriesFiles(event) && sectionIn(event.target);
    if (!section) return;
    event.preventDefault();
    section.classList.remove("is-drag-over");
    upload(section, event.dataTransfer.files);
  });

  document.addEventListener("paste", (event) => {
    const section = document.querySelector(sectionSelector);
    const clipboard = event.clipboardData;
    if (!section || !clipboard?.files.length) return;
    // Office apps add a picture of copied text; in a text field that stays a text paste.
    if (event.target.matches?.("input, textarea") && clipboard.types.includes("text/plain")) return;
    event.preventDefault();
    upload(section, clipboard.files);
  });
})();
```

- [ ] **Step 3: CSS** — insert after the `.ticket-development-empty` rule in `app/static/app.css`:

```css
.ticket-attachments { border: 2px dashed transparent; border-radius: var(--sketch-radius-card); display: grid; gap: var(--space-2); margin: 0 calc(-1 * var(--space-2)); padding: var(--space-2); transition: background-color 120ms ease, border-color 120ms ease; }
.ticket-attachments.is-drag-over { background: var(--sketch-post-it); border-color: var(--sketch-blue-ink); }
.ticket-attachments[aria-busy="true"] { cursor: progress; opacity: 0.7; }
.ticket-attachment-grid { display: flex; flex-wrap: wrap; gap: var(--space-3); list-style: none; margin: 0; padding: var(--space-2) var(--space-2) 0 0; }
.ticket-attachment-tile { flex: none; height: 96px; position: relative; width: 96px; }
.ticket-attachment-tile a { border: var(--sketch-border); border-radius: var(--sketch-radius-card); display: block; height: 100%; overflow: hidden; }
.ticket-attachment-tile a:focus-visible { outline: 3px solid var(--sketch-blue-ink); outline-offset: 2px; }
.ticket-attachment-tile img { display: block; height: 100%; object-fit: cover; width: 100%; }
.ticket-attachment-source { background: var(--surface); border-radius: 4px; bottom: 4px; font-size: 11px; font-weight: 700; left: 4px; padding: 0 4px; position: absolute; }
.ticket-attachment-delete { align-items: center; background: var(--surface); border: var(--sketch-border); border-radius: 50%; color: var(--sketch-pencil); cursor: pointer; display: inline-flex; flex: none; font-size: 16px; height: 28px; justify-content: center; line-height: 1; padding: 0; width: 28px; }
.ticket-attachment-delete:hover { border-color: var(--danger); color: var(--danger); }
.ticket-attachment-tile .ticket-attachment-delete { position: absolute; right: -10px; top: -10px; }
.ticket-attachment-files { display: grid; gap: var(--space-1); list-style: none; margin: 0; padding: 0; }
.ticket-attachment-file { align-items: center; display: flex; gap: var(--space-2); min-height: 40px; }
.ticket-attachment-file a { color: var(--sketch-blue-ink); min-width: 0; overflow-wrap: anywhere; }
.ticket-attachment-meta, .ticket-attachments-empty, .ticket-attachments-status { color: var(--muted); font-size: 12px; margin: 0; }
.ticket-attachment-meta { margin-right: auto; white-space: nowrap; }
.ticket-attachments-status:empty { display: none; }
@media (prefers-reduced-motion: reduce) { .ticket-attachments { transition: none; } }
```

- [ ] **Step 4: Browser check (one batched pass, desktop 1280×800 and mobile 390×844)**

Start the dev server against a scratch database in its own directory, so your data is not touched:

```bash
S=/private/tmp/claude-501/-Users-doyoungyoon-Desktop-kanbanflow/adfbae93-0ae1-492f-a840-1266f970953c/scratchpad
DATABASE_URL="sqlite:///$S/att.db" ATTACHMENTS_DIR="$S/att-files" uv run alembic upgrade head
DATABASE_URL="sqlite:///$S/att.db" ATTACHMENTS_DIR="$S/att-files" uv run uvicorn app.main:app --port 8765
```

(Run the server with `run_in_background`.) Write `$S/attachments_check.py` using system Playwright (`/opt/homebrew/opt/python@3.11/bin/python3.11`). It must:

1. Register, log in, create a project and ticket, and open the ticket detail drawer on the board.
2. Upload 12 images and 2 PDFs through `set_input_files` on `[data-attachment-input]`.
3. Dispatch a synthetic `drop` with a `DataTransfer` holding one PNG `File` on the panel.
4. Dispatch a synthetic `paste` on `document.body` with a `DataTransfer` holding one PNG. Also dispatch a `paste` on `#detail-description` with both `text/plain` and a PNG, then assert that one did **not** upload.
5. Check the new-tab link: `a[target=_blank]` is present, and its `href` returns `image/*` inline.
6. Delete one tile and accept the `confirm`.
7. Take desktop and mobile screenshots of the drawer.
8. Collect console errors.

Look at the screenshots and fix everything they show in one batch:

- Tiles wrap without squeezing the fields below.
- Delete buttons don't clip at the drawer edge.
- The Gmail badge is readable.
- The status text is aligned.
- Focus rings are visible.

Then confirm with at most one more round.

Expected: no console errors, text paste unaffected, counts match (12+1+1 images minus 1 deleted = 13 tiles, 2 files).

- [ ] **Step 5: Stage only your `app.css` hunk, then commit**

```bash
W=.superpowers/sdd/2026-09-29-ticket-attachments
diff -u "$W/app.css.before" app/static/app.css | sed -e '1s|.*|--- a/app/static/app.css|' -e '2s|.*|+++ b/app/static/app.css|' > "$W/attachments-css.patch"
git apply --cached "$W/attachments-css.patch"
git diff --cached --stat   # expect app/static/app.css with only your lines
git add app/static/app.js
git commit -m "feat: drop, paste, or pick files onto a ticket"
git diff --stat app/static/app.css   # the user's WIP hunk must still be unstaged
```

---

### Task 5: Gmail attachments

**Files:**
- Modify: `app/gmail_parse.py`
- Modify: `app/gmail.py` (`_get` timeout, `attachment`)
- Modify: `app/gmail_sync.py` (`_import_message`, new `_import_attachments`)
- Modify: `tests/gmail_messages.py` (`attachment_part`, `with_attachments`)
- Test: `tests/test_gmail_parse.py`, `tests/test_gmail_client.py`, `tests/test_gmail_sync.py`

**Interfaces:**
- Consumes: `attachments.store`, `discard`, `project_usage`, `ATTACHMENT_MAX_BYTES`, `PROJECT_QUOTA_BYTES` (Task 3); `create_ticket(..., attachment_rows=...)` (Task 3).
- Produces: `GmailAttachmentRef(filename, mime_type, size, attachment_id)`; `ParsedEmail.attachments: tuple[GmailAttachmentRef, ...]`, which replaces `attachment_count`; `GmailClient.attachment(access_token, message_id, attachment_id) -> bytes`.

- [ ] **Step 1: Test helpers** — append to `tests/gmail_messages.py`:

```python
def attachment_part(
    attachment_id: str,
    filename: str,
    size: int = 1234,
    mime_type: str = "application/pdf",
    *,
    content_id: str | None = None,
) -> dict:
    disposition = "inline" if content_id else "attachment"
    headers = [{"name": "Content-Disposition", "value": f'{disposition}; filename="{filename}"'}]
    if content_id:
        headers.append({"name": "Content-ID", "value": f"<{content_id}>"})
    return {
        "mimeType": mime_type,
        "filename": filename,
        "headers": headers,
        "body": {"attachmentId": attachment_id, "size": size},
    }


def with_attachments(*parts: dict, body: str = "See attached") -> dict:
    """A multipart/mixed payload: a plain-text body followed by `parts`."""
    return {
        "mimeType": "multipart/mixed",
        "headers": [
            {"name": "Subject", "value": "Need a flyer"},
            {"name": "From", "value": "Jane Doe <jane@example.com>"},
        ],
        "parts": [{"mimeType": "text/plain", "body": {"data": b64(body)}}, *parts],
    }
```

- [ ] **Step 2: Failing parser test** — in `tests/test_gmail_parse.py`:
  - Change line 19's `assert parsed.attachment_count == 0` to `assert parsed.attachments == ()`.
  - Replace `test_attachments_are_counted_not_decoded` with the test below.
  - Add `GmailAttachmentRef` to the `app.gmail_parse` import, and `attachment_part` and `with_attachments` to the `tests.gmail_messages` import.

```python
def test_real_attachments_are_listed_and_inline_images_skipped():
    payload = with_attachments(
        attachment_part("att-1", "brief.pdf", 1234),
        attachment_part("att-2", "logo.png", 99, "image/png", content_id="logo@sig"),
        attachment_part("att-3", "photo.jpg", 5000, "image/jpeg"),
    )
    parsed = parse_message(gmail_message(payload=payload))
    assert parsed.attachments == (
        GmailAttachmentRef("brief.pdf", "application/pdf", 1234, "att-1"),
        GmailAttachmentRef("photo.jpg", "image/jpeg", 5000, "att-3"),
    )
    assert parsed.body == "See attached"
    assert parsed.description == "From: Jane Doe <jane@example.com>\n\nSee attached"
```

- [ ] **Step 3: Failing client test** — append to `tests/test_gmail_client.py`:

```python
def test_attachment_downloads_and_decodes_the_data(gmail_settings):
    def handler(request):
        assert request.url.path == "/gmail/v1/users/me/messages/m1/attachments/att-1"
        assert request.headers["Authorization"] == "Bearer ya29.access"
        data = base64.urlsafe_b64encode(b"%PDF-1.4 brief").decode().rstrip("=")
        return httpx.Response(200, json={"size": 14, "data": data})

    with _client(gmail_settings, handler) as gmail:
        assert gmail.attachment("ya29.access", "m1", "att-1") == b"%PDF-1.4 brief"
```

Add `import base64` at the top.

- [ ] **Step 4: Failing sync tests** — in `tests/test_gmail_sync.py`:

Add imports:

```python
from pathlib import Path

from fastapi import HTTPException

from app import attachments
from app.models import AttachmentSource, TicketAttachment
from tests.gmail_messages import attachment_part, with_attachments
from tests.images import image_bytes
```

In `FakeGmail.__init__`, add `self.attachments = {}`, `self.failing_attachment_ids = set()`, and `self.attachment_calls = []`. Add this method:

```python
    def attachment(self, access_token, message_id, attachment_id):
        self.attachment_calls.append(attachment_id)
        if attachment_id in self.failing_attachment_ids:
            raise httpx.ConnectError("gmail unavailable")
        if attachment_id not in self.attachments:
            request = httpx.Request("GET", f"https://gmail.googleapis.com/attachments/{attachment_id}")
            raise httpx.HTTPStatusError(
                "gone", request=request, response=httpx.Response(404, request=request)
            )
        return self.attachments[attachment_id]
```

Append the tests:

```python
def _stored_files():
    root = Path(settings.attachments_dir)
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _attachment_rows(session):
    session.expire_all()
    return session.exec(select(TicketAttachment).order_by(TicketAttachment.filename)).all()


def _mail_with(*parts):
    return gmail_message("m1", payload=with_attachments(*parts))


def test_mail_attachments_are_stored_on_the_ticket(session, world):
    owner, project, connection = world
    gmail = FakeGmail(
        [
            _mail_with(
                attachment_part("att-1", "brief.pdf"),
                attachment_part("att-2", "shot.png", mime_type="image/png"),
                attachment_part("att-3", "logo.png", mime_type="image/png", content_id="sig"),
            )
        ]
    )
    gmail.attachments = {"att-1": b"%PDF-1.4 brief", "att-2": image_bytes()}
    sync_connection(session, connection, gmail)
    [ticket] = _tickets(session)
    brief, shot = _attachment_rows(session)
    assert (brief.filename, brief.is_image, shot.filename, shot.is_image) == (
        "brief.pdf",
        False,
        "shot.png",
        True,
    )
    assert {row.ticket_id for row in (brief, shot)} == {ticket.id}
    assert {row.source for row in (brief, shot)} == {AttachmentSource.GMAIL}
    assert {row.uploaded_by for row in (brief, shot)} == {owner.id}
    assert gmail.attachment_calls == ["att-1", "att-2"]  # the inline signature logo is skipped
    assert len(_stored_files()) == 2
    assert "not imported" not in ticket.description


def test_oversize_attachment_is_noted_and_never_downloaded(session, world):
    _, _, connection = world
    size = attachments.ATTACHMENT_MAX_BYTES + 1
    gmail = FakeGmail([_mail_with(attachment_part("att-1", "huge.zip", size, "application/zip"))])
    sync_connection(session, connection, gmail)
    [ticket] = _tickets(session)
    assert gmail.attachment_calls == []
    assert ticket.description.endswith("- huge.zip (10.0 MB) — not imported, open in Gmail")
    assert _attachment_rows(session) == []


def test_attachment_that_would_fill_the_project_is_noted(session, world, monkeypatch):
    _, _, connection = world
    monkeypatch.setattr(attachments, "PROJECT_QUOTA_BYTES", 100)
    gmail = FakeGmail([_mail_with(attachment_part("att-1", "brief.pdf", 1234))])
    gmail.attachments = {"att-1": b"x" * 1234}
    sync_connection(session, connection, gmail)
    [ticket] = _tickets(session)
    assert "brief.pdf (0.0 MB) — not imported, open in Gmail" in ticket.description
    assert gmail.attachment_calls == []


def test_attachment_gone_from_gmail_is_noted_and_the_ticket_still_imports(session, world):
    _, _, connection = world
    gmail = FakeGmail([_mail_with(attachment_part("att-1", "brief.pdf"))])
    sync_connection(session, connection, gmail)
    [ticket] = _tickets(session)
    assert "brief.pdf (0.0 MB) — not imported, open in Gmail" in ticket.description


def test_transient_attachment_failure_commits_nothing_and_replays_once(session, world):
    _, _, connection = world
    gmail = FakeGmail(
        [_mail_with(attachment_part("att-1", "brief.pdf"), attachment_part("att-2", "deck.pdf"))]
    )
    gmail.attachments = {"att-1": b"%PDF brief", "att-2": b"%PDF deck"}
    gmail.failing_attachment_ids = {"att-2"}
    with pytest.raises(httpx.ConnectError):
        sync_connection(session, connection, gmail)
    session.rollback()  # what sync_all does after a failed connection
    session.refresh(connection)
    assert connection.history_id == "100"
    assert _tickets(session) == [] and _attachment_rows(session) == [] and _stored_files() == []

    gmail.failing_attachment_ids = set()
    sync_connection(session, connection, gmail)
    assert len(_tickets(session)) == 1
    assert [row.filename for row in _attachment_rows(session)] == ["brief.pdf", "deck.pdf"]
    assert len(_stored_files()) == 2


def test_rejected_ticket_leaves_no_attachment_files(session, world, monkeypatch):
    _, _, connection = world

    def reject(*args, **kwargs):
        raise HTTPException(422, "title must not be empty")

    monkeypatch.setattr(gmail_sync, "create_ticket", reject)
    gmail = FakeGmail([_mail_with(attachment_part("att-1", "brief.pdf"))])
    gmail.attachments = {"att-1": b"%PDF brief"}
    sync_connection(session, connection, gmail)
    assert _tickets(session) == [] and _stored_files() == []
    session.refresh(connection)
    assert connection.last_error.startswith("Skipped Gmail message m1")
```

The oversize case is exactly `ATTACHMENT_MAX_BYTES + 1` bytes, which prints as `10.0 MB`. That shows the note uses MiB with one decimal, the same unit as the limit.

- [ ] **Step 5: Run them**

Run: `uv run pytest tests/test_gmail_parse.py tests/test_gmail_client.py tests/test_gmail_sync.py -q`
Expected: FAIL. `GmailAttachmentRef` import error first; after that, missing `attachment`/`attachments` behavior.

- [ ] **Step 6: Parser** — `app/gmail_parse.py`:

Replace the `ParsedEmail` dataclass with:

```python
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
```

Add after `_parts`:

```python
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
```

In `parse_message`, replace the `attachments = sum(...)` line and the return with:

```python
    return ParsedEmail(
        title=title, body=_truncate(body), sender=sender, attachments=_attachment_refs(payload)
    )
```

- [ ] **Step 7: Client** — `app/gmail.py`:

Add `import base64`. Give `_get` a `timeout: float = 10.0` keyword and pass it to `self._client.get(..., timeout=timeout)`. Append this method:

```python
    def attachment(self, access_token: str, message_id: str, attachment_id: str) -> bytes:
        body = self._get(
            access_token,
            f"messages/{quote(message_id, safe='')}/attachments/{quote(attachment_id, safe='')}",
            timeout=30.0,  # up to 10 MB of base64
        )
        data = body.get("data") or ""
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
```

- [ ] **Step 8: Sync** — `app/gmail_sync.py`:

Imports: add `from app import attachments` and `from app.gmail_parse import ParsedEmail`, and add `AttachmentSource` and `TicketAttachment` to the `app.models` import.

Add before `_import_message`:

```python
def _not_imported(filename: str, size: int) -> str:
    return f"- {filename} ({size / (1024 * 1024):.1f} MB) — not imported, open in Gmail"


def _import_attachments(
    session: Session,
    connection: GmailConnection,
    gmail: GmailClient,
    token: str,
    message: dict,
    parsed: ParsedEmail,
) -> tuple[list[TicketAttachment], list[str]]:
    """Download what fits. Transient errors propagate with nothing left on disk."""
    rows: list[TicketAttachment] = []
    notes: list[str] = []
    used = attachments.project_usage(session, connection.project_id)
    try:
        for ref in parsed.attachments:
            if (
                ref.size > attachments.ATTACHMENT_MAX_BYTES
                or used + ref.size > attachments.PROJECT_QUOTA_BYTES
            ):
                notes.append(_not_imported(ref.filename, ref.size))
                continue
            try:
                data = gmail.attachment(token, message["id"], ref.attachment_id)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                notes.append(_not_imported(ref.filename, ref.size))
                continue
            row = attachments.store(
                project_id=connection.project_id,
                uploaded_by=connection.user_id,
                filename=ref.filename,
                declared_type=ref.mime_type,
                data=data,
                source=AttachmentSource.GMAIL,
            )
            rows.append(row)
            used += row.size_bytes
    except BaseException:
        attachments.discard(rows)
        raise
    return rows, notes
```

Change `_import_message`'s signature to `(session, connection, gmail, token, message)`. After the `parsed = parse_message(message)` try/except block, and before `tasks = BackgroundTasks()`, add:

```python
    rows, notes = _import_attachments(session, connection, gmail, token, message, parsed)
    description = "\n\n".join([parsed.description, "\n".join(notes)]) if notes else parsed.description
    committed = False
```

In the `create_ticket(...)` call, use `description=description` and add `attachment_rows=rows`. Set `committed = True` right after the call, still inside the `try`. Then add a `finally:` to that `try`:

```python
    finally:
        if not committed:
            attachments.discard(rows)
```

In `sync_connection`, change the call to `_import_message(session, connection, gmail, token, message)`.

- [ ] **Step 9: Run the Gmail tests, then the full suite**

Run: `uv run pytest tests/test_gmail_parse.py tests/test_gmail_client.py tests/test_gmail_sync.py -q`
Expected: all pass.
Run: `uv run pytest -q > .superpowers/sdd/2026-09-29-ticket-attachments/task5.log 2>&1; tail -5 .superpowers/sdd/2026-09-29-ticket-attachments/task5.log`
Expected: all pass. Fix any remaining `attachment_count` references (`rtk proxy grep -rn attachment_count app tests`).

- [ ] **Step 10: Commit**

```bash
F=(app/gmail_parse.py app/gmail.py app/gmail_sync.py tests/gmail_messages.py tests/test_gmail_parse.py tests/test_gmail_client.py tests/test_gmail_sync.py)
uv run ruff format "${F[@]}" && uv run ruff check "${F[@]}"
git add "${F[@]}"
git commit -m "feat: import Gmail attachments with the ticket"
```

---

### Task 6: API and MCP

**Files:**
- Modify: `app/schemas.py` (`AttachmentOut`, `TicketDetailOut`)
- Modify: `app/routers/api_tickets.py` (`get_ticket` response, new bytes route)
- Modify: `app/mcp_server.py` (`api_request(raw=)`, `get_attachment`)
- Test: `tests/test_attachment_api.py`, `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `attachments.file_response`, `TicketAttachment`, the upload route (tests use it).
- Produces:
  - `GET /api/v1/tickets/{id}` → `TicketDetailOut` with `attachments: [{id, filename, content_type, size_bytes, is_image, source, created_at}]`.
  - `GET /api/v1/attachments/{id}` → bytes.
  - `api_request(..., raw=True) -> tuple[str, bytes]`.
  - MCP tool `get_attachment(attachment_id)`.

- [ ] **Step 1: Failing API tests** — `tests/test_attachment_api.py`:

```python
from sqlmodel import Session, select

from app.auth import make_csrf_token
from app.models import Ticket, TicketAttachment
from tests.images import image_bytes


def _world(make_user, make_project, add_member, engine):
    owner = make_user(email="ada@example.com")
    outsider = make_user(email="eve@example.com")
    project = make_project(owner)
    with Session(engine) as session:
        ticket = Ticket(ticket_number=1, project_id=project.id, title="Flyer", creator_id=owner.id)
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
    return owner, outsider, project, ticket


def test_ticket_lists_attachments_and_serves_their_bytes(
    client, make_user, make_project, add_member, engine, login_as
):
    owner, outsider, project, ticket = _world(make_user, make_project, add_member, engine)
    login_as(owner.email)
    png = image_bytes()
    client.post(
        f"/projects/{project.slug}/tickets/1/attachments",
        data={"_csrf": make_csrf_token(owner.id)},
        files=[("files", ("shot.png", png, "image/png"))],
        headers={"HX-Request": "true"},
    )
    with Session(engine) as session:
        row = session.exec(select(TicketAttachment)).one()

    body = client.get(f"/api/v1/tickets/{ticket.id}").json()
    [listed] = body["attachments"]
    assert listed["id"] == row.id
    assert (listed["filename"], listed["is_image"], listed["source"]) == ("shot.png", True, "UPLOAD")
    assert listed["content_type"] == "image/png" and listed["size_bytes"] == row.size_bytes

    response = client.get(f"/api/v1/attachments/{row.id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")

    login_as(outsider.email)
    assert client.get(f"/api/v1/attachments/{row.id}").status_code == 404
    assert client.get("/api/v1/attachments/missing").status_code == 404


def test_ticket_list_stays_without_attachments(client, make_user, make_project, add_member, engine, login_as):
    owner, _, project, _ = _world(make_user, make_project, add_member, engine)
    login_as(owner.email)
    [item] = client.get(f"/api/v1/projects/{project.slug}/tickets").json()["items"]
    assert "attachments" not in item
```

- [ ] **Step 2: Failing MCP tests** — append to `tests/test_mcp_server.py` (add `import base64`, `import io`, `from PIL import Image`, and `from tests.images import image_bytes` at the top):

```python
@pytest.mark.anyio
async def test_get_attachment_returns_an_image_sized_for_the_model(monkeypatch):
    calls = []

    async def fake_request(method, path, json=None, *, raw=False):
        calls.append((method, path, raw))
        return "image/png", image_bytes((3000, 1000))

    monkeypatch.setattr(mcp_server, "api_request", fake_request)
    async with Client(mcp) as client:
        result = await client.call_tool("get_attachment", {"attachment_id": "att/1"})

    assert calls == [("GET", "/api/v1/attachments/att%2F1", True)]
    [content] = result.content
    assert (content.type, content.mime_type) == ("image", "image/png")
    with Image.open(io.BytesIO(base64.b64decode(content.data))) as image:
        assert image.size == (1568, 523)


@pytest.mark.anyio
async def test_get_attachment_keeps_small_images_as_they_are(monkeypatch):
    png = image_bytes((800, 600))

    async def fake_request(method, path, json=None, *, raw=False):
        return "image/png", png

    monkeypatch.setattr(mcp_server, "api_request", fake_request)
    async with Client(mcp) as client:
        result = await client.call_tool("get_attachment", {"attachment_id": "att-1"})
    assert base64.b64decode(result.content[0].data) == png


@pytest.mark.anyio
async def test_get_attachment_refuses_non_images(monkeypatch):
    async def fake_request(method, path, json=None, *, raw=False):
        return "application/octet-stream", b"%PDF-1.4"

    monkeypatch.setattr(mcp_server, "api_request", fake_request)
    async with Client(mcp) as client:
        result = await client.call_tool("get_attachment", {"attachment_id": "att-1"})
    assert result.is_error is True
    assert "Only image attachments" in result.content[0].text


@pytest.mark.anyio
async def test_api_request_returns_type_and_bytes_when_raw(monkeypatch):
    monkeypatch.setenv("KANBANFLOW_API_TOKEN", "test-token")

    def handler(request):
        return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})

    assert await api_request(
        "GET", "/api/v1/attachments/att-1", raw=True, transport=httpx.MockTransport(handler)
    ) == ("image/png", b"\x89PNG")
```

Check how existing tests read the error flag and the image field (`result.is_error` / `isError`, `mime_type` / `mimeType`) against the MCP client's result type, and use whichever that version exposes.

- [ ] **Step 3: Run them**

Run: `uv run pytest tests/test_attachment_api.py tests/test_mcp_server.py -q`
Expected: FAIL. No `attachments` key, 404 on `/api/v1/attachments/…`, unknown tool `get_attachment`, and an unexpected `raw` kwarg.

- [ ] **Step 4: Schemas** — `app/schemas.py`, after `TicketOut`. Import `AttachmentSource` from `app.models` alongside the existing model imports.

```python
class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    content_type: str
    size_bytes: int
    is_image: bool
    source: AttachmentSource
    created_at: datetime


class TicketDetailOut(TicketOut):
    attachments: list[AttachmentOut] = []
```

- [ ] **Step 5: API routes** — `app/routers/api_tickets.py`:

Imports: add `Response` to the `fastapi` import, add `from app import attachments`, add `TicketAttachment` to the `app.models` import, and add `AttachmentOut` and `TicketDetailOut` to the `app.schemas` import.

Replace `get_ticket` with:

```python
@router.get("/tickets/{ticket_id}", response_model=TicketDetailOut)
def get_ticket(
    ticket_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TicketDetailOut:
    ticket, _, _ = load_ticket_for_read(ticket_id, user, session)
    rows = session.exec(
        select(TicketAttachment)
        .where(TicketAttachment.ticket_id == ticket.id)
        .order_by(TicketAttachment.created_at)
    ).all()
    return TicketDetailOut.model_validate(ticket).model_copy(
        update={"attachments": [AttachmentOut.model_validate(row) for row in rows]}
    )


@router.get("/attachments/{attachment_id}")
def get_attachment_content(
    attachment_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    row = session.get(TicketAttachment, attachment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    load_ticket_for_read(row.ticket_id, user, session)  # non-members get the same 404
    return attachments.file_response(row)
```

- [ ] **Step 6: MCP** — `app/mcp_server.py`:

Imports: add `import io`, `from mcp.server.mcpserver import Image`, and `from PIL import Image as PILImage`.

Add the constant `MCP_IMAGE_MAX_EDGE = 1568  # Claude resizes past this long edge anyway`.

In `api_request`, add the keyword `raw: bool = False` and replace the last line with:

```python
    if raw:
        return response.headers.get("content-type", ""), response.content
    return response.json() if response.content else None
```

Extract the error result so both callers share it:

```python
def _tool_error(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)
```

`_tool_request`'s `except` then returns `_tool_error(str(error))`.

Add after `get_ticket`:

```python
def _fit_for_model(data: bytes) -> bytes:
    with PILImage.open(io.BytesIO(data)) as image:
        if max(image.size) <= MCP_IMAGE_MAX_EDGE:
            return data
        image_format = image.format
        image.thumbnail((MCP_IMAGE_MAX_EDGE, MCP_IMAGE_MAX_EDGE))
        out = io.BytesIO()
        image.save(out, image_format)
        return out.getvalue()


@mcp.tool()
async def get_attachment(attachment_id: str) -> Image:
    """View an image attached to a ticket; get_ticket lists attachment ids. Other files are not readable here."""  # noqa: E501
    # ponytail: a non-image is fetched in full only to be refused; add a metadata route if
    # clients start calling this on large PDFs.
    try:
        content_type, data = await api_request(
            "GET", f"/api/v1/attachments/{_path_segment(attachment_id)}", raw=True
        )
    except ValueError as error:
        return _tool_error(str(error))
    if not content_type.startswith("image/"):
        return _tool_error(
            "Only image attachments are readable through MCP; get_ticket lists this file's name and size"  # noqa: E501
        )
    return Image(data=_fit_for_model(data), format=content_type.removeprefix("image/"))
```

- [ ] **Step 7: Run the tests, then the full suite**

Run: `uv run pytest tests/test_attachment_api.py tests/test_mcp_server.py -q`
Expected: all pass, including `test_mcp_server_loads_from_documented_cli_command`. That test runs `mcp run app/mcp_server.py:mcp`, so the new top-level Pillow import must work outside the `app` package, and it does because Pillow is a normal dependency.
Run: `uv run pytest -q > .superpowers/sdd/2026-09-29-ticket-attachments/task6.log 2>&1; tail -5 .superpowers/sdd/2026-09-29-ticket-attachments/task6.log`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
F=(app/schemas.py app/routers/api_tickets.py app/mcp_server.py tests/test_attachment_api.py tests/test_mcp_server.py)
uv run ruff format "${F[@]}" && uv run ruff check "${F[@]}"
git add "${F[@]}"
git commit -m "feat: list attachments in the API and let MCP view images"
```

---

### Task 7: Deployment settings and docs

**Files:**
- Modify: `deploy/nginx/kanbanflow.conf:18`
- Modify: `docker-compose.yml` (environment)
- Modify: `.env.example`
- Modify: `README.md` (new section after "Gmail ticket intake")
- Modify: `docs/superpowers/specs/2026-09-29-ticket-attachments-design.md` (record the plan's deviations)

- [ ] **Step 1: Edits**

`deploy/nginx/kanbanflow.conf`: `client_max_body_size 2m;` → `client_max_body_size 25m;  # several 10 MB attachments per upload`.

`docker-compose.yml`, under `environment:`:

```yaml
      ATTACHMENTS_DIR: /data/attachments
```

`.env.example`, at the end:

```
# Ticket attachments live on disk here (Docker uses /data/attachments, next to the database).
ATTACHMENTS_DIR=data/attachments
```

`README.md`, a new section after "Gmail ticket intake":

```markdown
## Ticket attachments

Drop files onto an open ticket, paste a screenshot with ⌘V, or use **Add files**. Images show as
thumbnails and open full size in a new tab; other files download. Mail imported by Gmail intake
brings its real attachments along (not inline signature images).

- Limits: 10 MB per file, 1 GB per project. Oversize mail attachments are listed in the ticket
  description instead of imported.
- Images over 2000 px on the long edge are downscaled on arrival and their EXIF (including GPS)
  is removed; the original is not kept. GIFs and animated images are stored as-is.
- Files are stored under `ATTACHMENTS_DIR` (`/data/attachments` in Docker), so back up `./data`
  as a whole. Nginx allows 25 MB request bodies for multi-file uploads.
- MCP: `get_ticket` lists attachments; `get_attachment` returns an image resized to 1568 px.
```

In the spec, add a `## Implementation Notes` section before "Out of Scope" with the four bullets from this plan's "Spec deviations" section, reworded as decisions.

- [ ] **Step 2: Verify config still loads and the suite passes**

Run: `uv run pytest -q > .superpowers/sdd/2026-09-29-ticket-attachments/task7.log 2>&1; tail -3 .superpowers/sdd/2026-09-29-ticket-attachments/task7.log`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add deploy/nginx/kanbanflow.conf docker-compose.yml .env.example README.md docs/superpowers/specs/2026-09-29-ticket-attachments-design.md
git commit -m "docs: document ticket attachments and raise the upload limit"
```

After merge, run `graphify update .` (AGENTS.md).
