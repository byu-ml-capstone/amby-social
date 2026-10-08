from sqlmodel import Session, select

from app.auth import make_csrf_token
from app.models import Ticket, TicketAttachment
from tests.images import image_bytes


def _world(make_user, make_project, engine):
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
    client, make_user, make_project, engine, login_as
):
    owner, outsider, project, ticket = _world(make_user, make_project, engine)
    login_as(owner.email)
    client.post(
        f"/projects/{project.slug}/tickets/1/attachments",
        data={"_csrf": make_csrf_token(owner.id)},
        files=[("files", ("shot.png", image_bytes(), "image/png"))],
        headers={"HX-Request": "true"},
    )
    with Session(engine) as session:
        row = session.exec(select(TicketAttachment)).one()

    body = client.get(f"/api/v1/tickets/{ticket.id}").json()
    [listed] = body["attachments"]
    assert listed["id"] == row.id
    assert (listed["filename"], listed["is_image"], listed["source"]) == (
        "shot.png",
        True,
        "UPLOAD",
    )
    assert listed["content_type"] == "image/png" and listed["size_bytes"] == row.size_bytes

    response = client.get(f"/api/v1/attachments/{row.id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")

    login_as(outsider.email)
    assert client.get(f"/api/v1/attachments/{row.id}").status_code == 404
    assert client.get("/api/v1/attachments/missing").status_code == 404


def test_ticket_list_stays_without_attachments(client, make_user, make_project, engine, login_as):
    owner, _, project, _ = _world(make_user, make_project, engine)
    login_as(owner.email)
    [item] = client.get(f"/api/v1/projects/{project.slug}/tickets").json()["items"]
    assert "attachments" not in item
