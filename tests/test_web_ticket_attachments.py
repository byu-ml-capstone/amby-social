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
    TicketStatus,
    utcnow,
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
    _upload(
        client, world, world.member, ("a.txt", b"a", "text/plain"), ("b.txt", b"b", "text/plain")
    )
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
            closed_at=utcnow(),
        )
        session.add(sprint)
        session.flush()
        session.add(
            SprintTicketHistory(
                sprint_id=sprint.id,
                ticket_id=world.ticket.id,
                status_at_close=TicketStatus.BACKLOG,
                was_completed=False,
            )
        )
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
