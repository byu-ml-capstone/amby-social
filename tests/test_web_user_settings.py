import hashlib

from sqlmodel import select

from app.auth import issue_api_token, make_csrf_token, verify_password
from app.models import ApiToken, ProjectMember, Role, User


def _csrf(user: User) -> dict[str, str]:
    return {"_csrf": make_csrf_token(user.id)}


def test_settings_page_is_linked_above_sign_out(client, make_user, login_as):
    user = make_user(email="ada@example.com", name="Ada")
    login_as(user.email)

    page = client.get("/settings")

    assert page.status_code == 200
    assert page.text.index('href="/settings"') < page.text.index('action="/logout"')
    assert "Personal information" in page.text
    assert "API tokens" in page.text
    assert "Delete account" in page.text


def test_profile_update_changes_name_and_email(client, session, make_user, login_as):
    user = make_user(email="ada@example.com", name="Ada")
    login_as(user.email)

    response = client.post(
        "/settings/profile",
        data={"name": "Ada Lovelace", "email": "ada@analytical.engine", **_csrf(user)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?profile_saved=1"
    updated = session.get(User, user.id)
    assert (updated.name, updated.email) == ("Ada Lovelace", "ada@analytical.engine")


def test_profile_update_rejects_another_users_email(client, session, make_user, login_as):
    user = make_user(email="ada@example.com", name="Ada")
    make_user(email="grace@example.com", name="Grace")
    login_as(user.email)

    response = client.post(
        "/settings/profile",
        data={"name": "Ada", "email": "grace@example.com", **_csrf(user)},
    )

    assert response.status_code == 409
    assert "already registered" in response.text
    assert session.get(User, user.id).email == "ada@example.com"


def test_password_change_requires_current_password(client, session, make_user, login_as):
    user = make_user(email="ada@example.com", password="old-password")
    login_as(user.email, "old-password")

    response = client.post(
        "/settings/password",
        data={
            "current_password": "wrong-password",
            "new_password": "new-password",
            "confirm_password": "new-password",
            **_csrf(user),
        },
    )

    assert response.status_code == 422
    assert "Current password is incorrect" in response.text
    assert verify_password("old-password", session.get(User, user.id).password_hash)


def test_password_change_replaces_login_password(client, make_user, login_as):
    user = make_user(email="ada@example.com", password="old-password")
    login_as(user.email, "old-password")

    response = client.post(
        "/settings/password",
        data={
            "current_password": "old-password",
            "new_password": "new-password",
            "confirm_password": "new-password",
            **_csrf(user),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    client.post("/logout", data=_csrf(user))
    assert (
        client.post("/login", data={"email": user.email, "password": "new-password"}).url.path
        == "/dashboard"
    )


def test_token_issue_lists_token_but_only_shows_secret_once(client, session, make_user, login_as):
    user = make_user(email="ada@example.com")
    login_as(user.email)

    issued = client.post(
        "/settings/tokens",
        data={"label": "Local MCP", **_csrf(user)},
    )

    token = session.exec(select(ApiToken).where(ApiToken.user_id == user.id)).one()
    assert issued.status_code == 201
    assert "Local MCP" in issued.text
    assert token.prefix in issued.text
    secret = issued.text.split('data-testid="issued-token">', 1)[1].split("</code>", 1)[0]
    assert secret.startswith("kf_")
    assert hashlib.sha256(secret.encode()).hexdigest() == token.token_hash
    assert secret not in client.get("/settings").text


def test_mcp_setup_tutorial_only_appears_on_token_issue(client, make_user, login_as):
    user = make_user(email="ada@example.com")
    login_as(user.email)

    issued = client.post(
        "/settings/tokens",
        data={"label": "Local MCP", **_csrf(user)},
    )

    assert "Connect your MCP client" in issued.text
    assert "claude mcp add" in issued.text
    assert "codex mcp add" in issued.text
    assert "~/.cursor/mcp.json" in issued.text
    assert '"mcpServers"' in issued.text
    assert "KANBANFLOW_API_TOKEN" in issued.text
    assert "http://testserver/mcp" in issued.text
    assert "/path/to" not in issued.text
    assert "Connect your MCP client" not in client.get("/settings").text


def test_token_revoke_cannot_revoke_another_users_token(client, session, make_user, login_as):
    user = make_user(email="ada@example.com")
    other = make_user(email="grace@example.com")
    other_token, _ = issue_api_token(session, other, "Grace MCP")
    login_as(user.email)

    response = client.post(
        f"/settings/tokens/{other_token.id}/revoke",
        data=_csrf(user),
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(other_token)
    assert other_token.revoked_at is None


def test_token_revoke_marks_own_token_revoked(client, session, make_user, login_as):
    user = make_user(email="ada@example.com")
    token, _ = issue_api_token(session, user, "Local MCP")
    login_as(user.email)

    response = client.post(
        f"/settings/tokens/{token.id}/revoke",
        data=_csrf(user),
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(token)
    assert token.revoked_at is not None


def test_account_delete_is_blocked_for_a_projects_only_owner(
    client, session, make_user, make_project, login_as
):
    user = make_user(email="ada@example.com", password="password123")
    project = make_project(user, name="Payment Gateway")
    login_as(user.email, "password123")

    response = client.post(
        "/settings/delete",
        data={"current_password": "password123", **_csrf(user)},
    )

    assert response.status_code == 409
    assert project.name in response.text
    assert session.get(User, user.id).deleted_at is None


def test_account_delete_requires_current_password(client, session, make_user, login_as):
    user = make_user(email="ada@example.com", password="password123")
    login_as(user.email, "password123")

    response = client.post(
        "/settings/delete",
        data={"current_password": "wrong-password", **_csrf(user)},
    )

    assert response.status_code == 422
    assert "Current password is incorrect" in response.text
    assert session.get(User, user.id).deleted_at is None


def test_account_delete_anonymizes_user_and_removes_access(
    client, session, make_user, make_project, add_member, login_as
):
    user = make_user(email="ada@example.com", name="Ada", password="password123")
    other_owner = make_user(email="grace@example.com")
    project = make_project(user, name="Payment Gateway")
    add_member(project, other_owner, Role.OWNER)
    issue_api_token(session, user, "Local MCP")
    login_as(user.email, "password123")

    response = client.post(
        "/settings/delete",
        data={"current_password": "password123", **_csrf(user)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    deleted = session.get(User, user.id)
    assert deleted.deleted_at is not None
    assert deleted.name == "Deleted user"
    assert deleted.email != "ada@example.com"
    assert session.exec(select(ApiToken).where(ApiToken.user_id == user.id)).all() == []
    assert session.exec(select(ProjectMember).where(ProjectMember.user_id == user.id)).all() == []
    assert (
        client.post(
            "/login", data={"email": "ada@example.com", "password": "password123"}
        ).status_code
        == 401
    )
