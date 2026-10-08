def test_same_origin_write_is_allowed(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        "/api/v1/tickets",
        json={"slug": project.slug, "title": "T"},
        headers={"Origin": "http://localhost:8000"},
    )
    assert response.status_code == 201


def test_foreign_origin_write_is_rejected(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        "/api/v1/tickets",
        json={"slug": project.slug, "title": "T"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 403


def test_missing_origin_is_allowed(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post("/api/v1/tickets", json={"slug": project.slug, "title": "T"})
    assert response.status_code == 201


def test_reads_are_not_blocked_by_origin(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.get(
        f"/api/v1/projects/{project.slug}", headers={"Origin": "https://evil.example.com"}
    )
    assert response.status_code == 200


def test_missing_cookie_write_is_not_blocked_by_origin(client, make_user, make_project):
    # No login_as call: this client carries no session cookie. A foreign Origin plus an
    # unsafe method plus an /api/v1/ path is not enough on its own -- the guard requires
    # the cookie too, so this must fail on auth (401), not on the origin guard (403).
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    response = client.post(
        "/api/v1/tickets",
        json={"slug": project.slug, "title": "T"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 401


def test_non_api_path_write_is_not_blocked_by_origin(client, make_user, make_project, login_as):
    # Same cookie, same foreign Origin, same unsafe method as the rejected case, but the
    # path is a web (HTML) route, not /api/v1/. The origin guard must not act here --
    # this surface is covered by verify_csrf instead -- so a valid CSRF token still lets
    # the write through despite the foreign Origin.
    from app.auth import make_csrf_token

    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    token = make_csrf_token(owner.id)
    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "T", "_csrf": token},
        headers={"Origin": "https://evil.example.com"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/projects/{project.slug}"


def test_foreign_origin_write_creates_no_ticket(client, session, make_user, make_project, login_as):
    from sqlmodel import select

    from app.models import Ticket

    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        "/api/v1/tickets",
        json={"slug": project.slug, "title": "T"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert session.exec(select(Ticket)).first() is None
