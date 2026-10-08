from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
from fastapi import HTTPException, Response
from sqlmodel import Session, select

import app.routers.web_sprints as web_sprints
from app.auth import make_csrf_token
from app.config import settings
from app.github import AvailableRepository, issue_github_state, read_github_state
from app.github import GitHubClient as RealGitHubClient
from app.models import (
    GitHubArtifact,
    GitHubArtifactKind,
    GitHubArtifactState,
    GitHubCIState,
    GitHubInstallation,
    Project,
    ProjectGitHubConnection,
    ProjectGitHubRepository,
    ProjectMember,
    Role,
    WebhookType,
    utcnow,
)
from app.services import chat_webhooks, set_chat_webhook


@pytest.fixture
def settings_world(make_user, make_project, add_member):
    owner = make_user(email="ada@example.com", name="Ada")
    member = make_user(email="bob@example.com", name="Bob")
    candidate = make_user(email="cam@example.com")
    project = make_project(owner, name="Payment Gateway")
    add_member(project, member)
    return SimpleNamespace(owner=owner, member=member, candidate=candidate, project=project)


def _csrf(user):
    return {"_csrf": make_csrf_token(user.id)}


@pytest.fixture
def configured_github(monkeypatch):
    for name, value in {
        "github_app_id": "12345",
        "github_app_slug": "kanban-flow",
        "github_client_id": "client-id",
        "github_client_secret": "client-secret",
        "github_private_key": "test-private-key",
        "github_webhook_secret": "webhook-secret",
    }.items():
        monkeypatch.setattr(settings, name, value)


def _use_github_transport(monkeypatch, handler):
    transport = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(RealGitHubClient, "_app_token", lambda self: "test-app-jwt")
    monkeypatch.setattr(
        web_sprints,
        "GitHubClient",
        lambda: RealGitHubClient(settings, transport),
        raising=False,
    )
    return transport


def _assert_state_used(session, state, user):
    session.expire_all()
    with pytest.raises(HTTPException, match="already used"):
        read_github_state(session, state, user.id)


@pytest.fixture
def github_selection(
    settings_world, session, monkeypatch, configured_github
):
    installation = GitHubInstallation(
        github_installation_id=7001,
        account_id=91,
        account_login="acme",
        connected_by_id=settings_world.owner.id,
    )
    session.add(installation)
    session.commit()
    session.refresh(installation)
    repository_calls = []

    def handler(request):
        if request.url.path == "/app/installations/7001/access_tokens":
            return httpx.Response(201, json={"token": "ghs_selection_token"})
        if request.url.path.startswith("/repos/") and request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        assert request.url.path == "/installation/repositories"
        repository_calls.append(request)
        return httpx.Response(
            200,
            json={
                "repositories": [
                    {
                        "id": 501,
                        "full_name": "acme/api",
                        "html_url": "https://github.com/acme/api",
                        "default_branch": "main",
                    },
                    {
                        "id": 502,
                        "full_name": "acme/web",
                        "html_url": "https://github.com/acme/web",
                        "default_branch": "trunk",
                    },
                ]
            },
        )

    transport = _use_github_transport(monkeypatch, handler)
    state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=installation.github_installation_id,
    )
    yield SimpleNamespace(
        installation=installation,
        repository_calls=repository_calls,
        state=state,
    )
    transport.close()


@pytest.fixture
def connected_github(settings_world, session):
    installation = GitHubInstallation(
        github_installation_id=7001,
        account_id=91,
        account_login="acme-inc",
        connected_by_id=settings_world.owner.id,
    )
    session.add(installation)
    session.flush()
    session.add(
        ProjectGitHubConnection(
            project_id=settings_world.project.id,
            installation_id=installation.id,
        )
    )
    repository = ProjectGitHubRepository(
        project_id=settings_world.project.id,
        installation_id=installation.id,
        github_repository_id=501,
        full_name="acme/api",
        html_url="https://github.com/acme/api",
        default_branch="main",
    )
    session.add(repository)
    session.commit()
    session.refresh(repository)
    return repository


def test_owner_sees_integration_and_member_controls(client, settings_world, login_as):
    login_as(settings_world.owner.email)

    page = client.get(f"/projects/{settings_world.project.slug}/settings")

    assert page.status_code == 200
    assert "Project settings" in page.text
    assert "Payment Gateway" in page.text
    assert "ada@example.com" in page.text
    assert "bob@example.com" in page.text
    assert 'data-testid="owner-settings-controls"' in page.text
    assert 'name="url"' in page.text
    assert all(provider in page.text for provider in ("Slack", "Teams", "Discord", "GitHub"))
    assert "GitHub App settings not configured" in page.text
    assert "Connect GitHub" in page.text
    assert "disabled" in page.text
    assert 'name="confirm"' in page.text


def test_settings_member_controls_name_members_and_use_a_danger_action(
    client, settings_world, login_as
):
    """Removing a member name or danger styling must make this fail."""
    login_as(settings_world.owner.email)

    page = client.get(f"/projects/{settings_world.project.slug}/settings")

    assert 'aria-label="Role for Bob"' in page.text
    assert 'aria-label="Change role for Bob"' in page.text
    assert 'aria-label="Remove Bob"' in page.text
    assert '<label class="sr-only" for="add-member-role">Role for new member</label>' in page.text
    assert 'action="/projects/payment-gateway/settings/delete"' in page.text
    assert (
        'class="app-danger-button" type="submit" aria-label="Remove Bob">Remove</button>'
        in page.text
    )
    assert 'class="app-danger-button" type="submit">Delete project</button>' in page.text


def test_project_settings_save_redirects_to_a_textual_status(
    client, settings_world, login_as
):
    login_as(settings_world.owner.email)

    response = client.post(
        f"/projects/{settings_world.project.slug}/settings/project",
        data={"name": "Checkout", **_csrf(settings_world.owner)},
        follow_redirects=False,
    )

    assert response.headers["location"] == (
        f"/projects/{settings_world.project.slug}/settings?saved=1"
    )
    page = client.get(response.headers["location"])
    assert '<p class="form-status" role="status">Settings saved.</p>' in page.text


def test_member_reads_settings_without_mutation_controls_or_webhook_secret(
    client, settings_world, session, login_as
):
    project = session.get(Project, settings_world.project.id)
    set_chat_webhook(
        session, project, WebhookType.SLACK, "https://hooks.example.test/secret"
    )
    login_as(settings_world.member.email)

    page = client.get(f"/projects/{settings_world.project.slug}/settings")

    assert page.status_code == 200
    assert "Payment Gateway" in page.text
    assert "ada@example.com" in page.text
    assert "Connected" in page.text
    assert "https://hooks.example.test/secret" not in page.text
    assert 'name="url"' not in page.text
    assert 'data-testid="owner-settings-controls"' not in page.text
    assert f'action="/projects/{settings_world.project.slug}/settings/' not in page.text
    assert "GitHub App settings not configured" not in page.text
    assert "Connect GitHub" not in page.text


def test_owner_starts_github_connect(client, settings_world, login_as, configured_github):
    login_as(settings_world.owner.email)
    response = client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/connect",
        data=_csrf(settings_world.owner),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith(
        "https://github.com/apps/kanban-flow/installations/new?state="
    )


def test_member_and_missing_csrf_cannot_start_github_connect(
    client, settings_world, login_as, configured_github
):
    url = f"/projects/{settings_world.project.slug}/settings/integrations/github/connect"
    login_as(settings_world.member.email)
    assert client.post(url, data=_csrf(settings_world.member)).status_code == 403
    login_as(settings_world.owner.email)
    assert client.post(url, data={}).status_code == 403


def test_github_setup_consumes_state_and_redirects_with_fresh_oauth_state(
    client, settings_world, session, login_as, configured_github
):
    setup_state = issue_github_state(
        session, settings_world.project.id, settings_world.owner.id
    )
    login_as(settings_world.owner.email)

    response = client.get(
        f"/integrations/github/setup?installation_id=7001&state={quote(setup_state)}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert (location.scheme, location.netloc, location.path) == (
        "https",
        "github.com",
        "/login/oauth/authorize",
    )
    query = parse_qs(location.query)
    assert query["client_id"] == ["client-id"]
    oauth_state = query["state"][0]
    _assert_state_used(session, setup_state, settings_world.owner)
    assert (
        read_github_state(session, oauth_state, settings_world.owner.id).pending_installation_id
        == 7001
    )


def test_github_setup_consumes_cancellation_and_rejects_invalid_installation_id(
    client, settings_world, session, login_as, configured_github
):
    login_as(settings_world.owner.email)
    cancelled_state = issue_github_state(
        session, settings_world.project.id, settings_world.owner.id
    )
    cancelled = client.get(
        f"/integrations/github/setup?state={quote(cancelled_state)}",
        follow_redirects=False,
    )
    assert cancelled.status_code == 303
    assert cancelled.headers["location"].endswith("?github_error=installation_cancelled")
    _assert_state_used(session, cancelled_state, settings_world.owner)

    invalid_state = issue_github_state(session, settings_world.project.id, settings_world.owner.id)
    invalid = client.get(
        f"/integrations/github/setup?installation_id=nope&state={quote(invalid_state)}"
    )
    assert invalid.status_code == 400
    _assert_state_used(session, invalid_state, settings_world.owner)


def test_github_oauth_callback_verifies_then_persists_and_issues_selection_state(
    client, settings_world, session, login_as, configured_github, monkeypatch
):
    requests = []
    metadata = {"id": 7001, "account": {"id": 91, "login": "acme"}}

    def handler(request):
        requests.append(request)
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "ghu_callback_token"})
        if request.url.path == "/user/installations/7001/repositories":
            assert request.headers["Authorization"] == "Bearer ghu_callback_token"
            return httpx.Response(200, json={"repositories": []})
        assert request.url.path == "/app/installations/7001"
        return httpx.Response(200, json=metadata)

    transport = _use_github_transport(monkeypatch, handler)
    oauth_state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=7001,
    )
    login_as(settings_world.owner.email)

    response = client.get(
        f"/integrations/github/callback?code=oauth-code&state={quote(oauth_state)}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert location.path == (
        f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories"
    )
    selection_state = parse_qs(location.query)["state"][0]
    _assert_state_used(session, oauth_state, settings_world.owner)
    assert (
        read_github_state(session, selection_state, settings_world.owner.id).pending_installation_id
        == 7001
    )
    installation = session.exec(select(GitHubInstallation)).one()
    session.refresh(installation)
    assert (installation.github_installation_id, installation.account_id) == (7001, 91)
    assert (installation.account_login, installation.connected_by_id) == (
        "acme",
        settings_world.owner.id,
    )
    assert "ghu_callback_token" not in repr(installation)
    assert [request.url.path for request in requests] == [
        "/login/oauth/access_token",
        "/user/installations/7001/repositories",
        "/app/installations/7001",
    ]
    metadata["account"] = {"id": 92, "login": "acme-renamed"}
    replacement_state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=7001,
    )
    assert client.get(
        f"/integrations/github/callback?code=oauth-code&state={quote(replacement_state)}",
        follow_redirects=False,
    ).status_code == 303
    session.expire_all()
    installations = session.exec(select(GitHubInstallation)).all()
    assert len(installations) == 1
    assert (installations[0].account_id, installations[0].account_login) == (
        92,
        "acme-renamed",
    )
    transport.close()


def test_github_oauth_callback_consumes_denial_and_missing_code(
    client, settings_world, session, login_as, configured_github
):
    login_as(settings_world.owner.email)
    denied_state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=7001,
    )
    denied = client.get(
        f"/integrations/github/callback?error=access_denied&state={quote(denied_state)}",
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert denied.headers["location"].endswith("?github_error=oauth_denied")
    _assert_state_used(session, denied_state, settings_world.owner)

    missing_state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=7001,
    )
    missing = client.get(f"/integrations/github/callback?state={quote(missing_state)}")
    assert missing.status_code == 400
    _assert_state_used(session, missing_state, settings_world.owner)


def test_github_oauth_callback_rejects_replay_expiry_and_wrong_user(
    client, settings_world, session, login_as, configured_github
):
    login_as(settings_world.owner.email)
    replay_state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=7001,
    )
    assert client.get(
        f"/integrations/github/callback?error=access_denied&state={quote(replay_state)}"
    ).status_code == 200
    assert client.get(
        f"/integrations/github/callback?code=oauth-code&state={quote(replay_state)}"
    ).status_code == 400

    expired_state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=7001,
        now=utcnow() - timedelta(minutes=11),
    )
    assert client.get(
        f"/integrations/github/callback?code=oauth-code&state={quote(expired_state)}"
    ).status_code == 400

    wrong_user_state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=7001,
    )
    client.post("/api/v1/auth/logout")
    login_as(settings_world.member.email)
    assert client.get(
        f"/integrations/github/callback?code=oauth-code&state={quote(wrong_user_state)}"
    ).status_code == 403


def test_github_oauth_callback_verification_failure_persists_no_installation(
    client, settings_world, session, login_as, configured_github, monkeypatch
):
    def handler(request):
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "ghu_rejected_token"})
        return httpx.Response(404)

    transport = _use_github_transport(monkeypatch, handler)
    oauth_state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=7001,
    )
    login_as(settings_world.owner.email)

    response = client.get(
        f"/integrations/github/callback?code=oauth-code&state={quote(oauth_state)}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("?github_error=verification_failed")
    _assert_state_used(session, oauth_state, settings_world.owner)
    assert session.exec(select(GitHubInstallation)).all() == []
    transport.close()


@pytest.mark.parametrize(
    "installation_payload",
    [
        [],
        {"id": "7001", "account": {"id": 91, "login": "acme"}},
        {"id": 7001, "account": {"id": "91", "login": "acme"}},
        {"id": 7001, "account": {"id": 91, "login": ""}},
    ],
)
def test_github_oauth_callback_rejects_invalid_installation_metadata(
    client,
    settings_world,
    session,
    login_as,
    configured_github,
    monkeypatch,
    installation_payload,
):
    def handler(request):
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "ghu_callback_token"})
        if request.url.path == "/user/installations/7001/repositories":
            return httpx.Response(200, json={"repositories": []})
        return httpx.Response(200, json=installation_payload)

    transport = _use_github_transport(monkeypatch, handler)
    oauth_state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=7001,
    )
    login_as(settings_world.owner.email)

    response = client.get(
        f"/integrations/github/callback?code=oauth-code&state={quote(oauth_state)}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("?github_error=verification_failed")
    assert session.exec(select(GitHubInstallation)).all() == []
    transport.close()


def test_github_repository_selection_get_keeps_state_and_renders_server_repositories(
    client, settings_world, session, login_as, github_selection
):
    login_as(settings_world.owner.email)

    page = client.get(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories"
        f"?state={quote(github_selection.state)}"
    )

    assert page.status_code == 200
    assert 'value="501"' in page.text
    assert 'value="502"' in page.text
    assert "acme/api" in page.text and "acme/web" in page.text
    assert f'name="state" value="{github_selection.state}"' in page.text
    assert (
        read_github_state(session, github_selection.state, settings_world.owner.id).consumed_at
        is None
    )


def test_github_repository_selection_post_refetches_and_trusts_only_server_metadata(
    client, settings_world, session, login_as, github_selection
):
    login_as(settings_world.owner.email)
    page_url = (
        f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories"
        f"?state={quote(github_selection.state)}"
    )
    assert client.get(page_url).status_code == 200

    saved = client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories",
        data={
            "state": github_selection.state,
            "repository_ids": ["501", "502"],
            "full_name": "attacker/forged",
            "html_url": "javascript:alert(1)",
            "default_branch": "evil",
            **_csrf(settings_world.owner),
        },
        follow_redirects=False,
    )

    assert saved.status_code == 303
    assert saved.headers["location"] == f"/projects/{settings_world.project.slug}/settings"
    assert len(github_selection.repository_calls) == 2
    _assert_state_used(session, github_selection.state, settings_world.owner)
    session.expire_all()
    rows = session.exec(
        select(ProjectGitHubRepository).order_by(
            ProjectGitHubRepository.github_repository_id
        )
    ).all()
    assert [row.github_repository_id for row in rows] == [501, 502]
    assert [(row.full_name, row.html_url, row.default_branch) for row in rows] == [
        ("acme/api", "https://github.com/acme/api", "main"),
        ("acme/web", "https://github.com/acme/web", "trunk"),
    ]
    assert all(row.active and row.disconnected_at is None for row in rows)

    reduced_state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=7001,
    )
    assert client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories",
        data={
            "state": reduced_state,
            "repository_ids": ["501"],
            **_csrf(settings_world.owner),
        },
        follow_redirects=False,
    ).status_code == 303
    session.expire_all()
    omitted = session.exec(
        select(ProjectGitHubRepository).where(
            ProjectGitHubRepository.github_repository_id == 502
        )
    ).one()
    assert omitted.active is False and omitted.disconnected_at is not None

    restored_state = issue_github_state(
        session,
        settings_world.project.id,
        settings_world.owner.id,
        pending_installation_id=7001,
    )
    assert client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories",
        data={
            "state": restored_state,
            "repository_ids": ["501", "502"],
            **_csrf(settings_world.owner),
        },
        follow_redirects=False,
    ).status_code == 303
    session.refresh(omitted)
    assert omitted.active is True and omitted.disconnected_at is None


def test_selection_commits_before_independent_repository_sync(
    client, engine, settings_world, login_as, github_selection, monkeypatch
):
    seen = []

    def fake_sync(session, connection, github):
        with Session(engine) as observer:
            assert observer.get(ProjectGitHubRepository, connection.id).active is True
        seen.append(connection.github_repository_id)
        if connection.github_repository_id == 502:
            raise httpx.ConnectError("repository unavailable")
        session.add(
            GitHubArtifact(
                repository_connection_id=connection.id,
                kind=GitHubArtifactKind.PULL_REQUEST,
                external_id="pr-501",
                number=17,
                title="PAY-1",
                html_url="https://github.com/acme/api/pull/17",
                author_login="sam",
                state=GitHubArtifactState.OPEN,
                ci_state=GitHubCIState.NONE,
                occurred_at=utcnow(),
            )
        )

    monkeypatch.setattr(web_sprints, "sync_open_pull_requests", fake_sync, raising=False)
    login_as(settings_world.owner.email)
    response = client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories",
        data={
            "state": github_selection.state,
            "repository_ids": ["501", "502"],
            **_csrf(settings_world.owner),
        },
    )
    assert response.status_code == 200
    assert seen == [501, 502]
    assert "Retry sync" in response.text and "acme/web" in response.text
    with Session(engine) as observer:
        assert len(observer.exec(select(ProjectGitHubRepository)).all()) == 2
        assert len(observer.exec(select(GitHubArtifact)).all()) == 1


def test_selection_does_not_swallow_internal_sync_errors(
    client, engine, settings_world, login_as, github_selection, monkeypatch
):
    def explode(session, connection, github):
        raise RuntimeError("sync invariant failed")

    monkeypatch.setattr(web_sprints, "sync_open_pull_requests", explode, raising=False)
    login_as(settings_world.owner.email)
    with pytest.raises(RuntimeError, match="sync invariant failed"):
        client.post(
            f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories",
            data={
                "state": github_selection.state,
                "repository_ids": ["501", "502"],
                **_csrf(settings_world.owner),
            },
        )
    with Session(engine) as observer:
        assert len(observer.exec(select(ProjectGitHubRepository)).all()) == 2


def test_github_repository_selection_rejects_forged_id_and_consumes_state(
    client, settings_world, session, login_as, github_selection
):
    login_as(settings_world.owner.email)

    rejected = client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories",
        data={
            "state": github_selection.state,
            "repository_ids": ["999"],
            **_csrf(settings_world.owner),
        },
    )

    assert rejected.status_code == 422
    _assert_state_used(session, github_selection.state, settings_world.owner)
    assert session.exec(select(ProjectGitHubRepository)).all() == []
    assert client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories",
        data={
            "state": github_selection.state,
            "repository_ids": ["501"],
            **_csrf(settings_world.owner),
        },
    ).status_code == 400


def test_github_repository_selection_rejects_oversized_decimal_id(
    client, settings_world, session, login_as, github_selection
):
    login_as(settings_world.owner.email)

    response = client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories",
        data={
            "state": github_selection.state,
            "repository_ids": ["9" * 5_000],
            **_csrf(settings_world.owner),
        },
    )

    assert response.status_code == 422
    _assert_state_used(session, github_selection.state, settings_world.owner)
    assert session.exec(select(ProjectGitHubRepository)).all() == []


def test_github_repository_selection_requires_owner_and_csrf_without_consuming_state(
    client, settings_world, session, login_as, github_selection
):
    url = f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories"
    login_as(settings_world.member.email)
    assert client.get(f"{url}?state={quote(github_selection.state)}").status_code == 403
    login_as(settings_world.owner.email)
    assert client.post(
        url,
        data={"state": github_selection.state, "repository_ids": ["501"]},
    ).status_code == 403
    assert (
        read_github_state(session, github_selection.state, settings_world.owner.id).consumed_at
        is None
    )


def test_github_repository_selection_binds_state_to_slug_project(
    client,
    settings_world,
    session,
    login_as,
    make_project,
    github_selection,
):
    other_project = make_project(settings_world.owner, name="Other Project")
    url = f"/projects/{other_project.slug}/settings/integrations/github/repositories"
    login_as(settings_world.owner.email)

    assert client.get(
        f"{url}?state={quote(github_selection.state)}"
    ).status_code == 400
    assert (
        read_github_state(session, github_selection.state, settings_world.owner.id).consumed_at
        is None
    )
    assert client.post(
        url,
        data={
            "state": github_selection.state,
            "repository_ids": ["501"],
            **_csrf(settings_world.owner),
        },
    ).status_code == 400
    _assert_state_used(session, github_selection.state, settings_world.owner)


def test_github_repository_selection_rejects_another_project_installation_when_inactive(
    session, settings_world
):
    from app.github_sync import save_project_repositories

    first = GitHubInstallation(
        github_installation_id=7001,
        account_id=91,
        account_login="acme",
        connected_by_id=settings_world.owner.id,
    )
    second = GitHubInstallation(
        github_installation_id=7002,
        account_id=92,
        account_login="other",
        connected_by_id=settings_world.owner.id,
    )
    session.add_all([first, second])
    session.flush()
    session.add(
        ProjectGitHubConnection(
            project_id=settings_world.project.id,
            installation_id=first.id,
        )
    )
    session.add(
        ProjectGitHubRepository(
            project_id=settings_world.project.id,
            installation_id=first.id,
            github_repository_id=501,
            full_name="acme/api",
            html_url="https://github.com/acme/api",
            default_branch="main",
            active=False,
            disconnected_at=utcnow(),
        )
    )
    session.commit()

    with pytest.raises(HTTPException, match="already uses another GitHub installation"):
        save_project_repositories(
            session,
            settings_world.project,
            second,
            [
                AvailableRepository(
                    601,
                    "other/api",
                    "https://github.com/other/api",
                    "main",
                )
            ],
        )


def test_github_disconnect_requires_owner_csrf_and_confirmation(
    client, settings_world, session, login_as, connected_github
):
    url = f"/projects/{settings_world.project.slug}/settings/integrations/github/disconnect"
    login_as(settings_world.member.email)
    assert client.post(
        url,
        data={"confirm": "Disconnect GitHub", **_csrf(settings_world.member)},
    ).status_code == 403
    login_as(settings_world.owner.email)
    assert client.post(url, data={"confirm": "Disconnect GitHub"}).status_code == 403
    assert client.post(
        url,
        data={"confirm": "wrong", **_csrf(settings_world.owner)},
    ).status_code == 422
    assert connected_github.active is True

    response = client.post(
        url,
        data={"confirm": "Disconnect GitHub", **_csrf(settings_world.owner)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(connected_github)
    assert connected_github.active is False
    assert connected_github.disconnected_at is not None


def test_retry_sync_requires_owner_and_csrf(
    client, settings_world, login_as, connected_github, monkeypatch
):
    monkeypatch.setattr(
        web_sprints,
        "sync_open_pull_requests",
        lambda session, connection, github: 0,
        raising=False,
    )
    url = f"/projects/{settings_world.project.slug}/settings/integrations/github/sync"
    login_as(settings_world.member.email)
    assert client.post(url, data=_csrf(settings_world.member)).status_code == 403
    login_as(settings_world.owner.email)
    assert client.post(url, data={}).status_code == 403
    response = client.post(
        url, data=_csrf(settings_world.owner), follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("?github_synced=1")


def test_retry_syncs_active_repositories_independently_without_reactivating_inactive(
    client, engine, settings_world, session, login_as, connected_github, monkeypatch
):
    second = ProjectGitHubRepository(
        project_id=settings_world.project.id,
        installation_id=connected_github.installation_id,
        github_repository_id=502,
        full_name="acme/web",
        html_url="https://github.com/acme/web",
        default_branch="trunk",
    )
    inactive = ProjectGitHubRepository(
        project_id=settings_world.project.id,
        installation_id=connected_github.installation_id,
        github_repository_id=503,
        full_name="acme/retired",
        html_url="https://github.com/acme/retired",
        default_branch="main",
        active=False,
        disconnected_at=utcnow(),
    )
    session.add_all([second, inactive])
    session.commit()
    seen = []

    def fake_sync(sync_session, connection, github):
        seen.append(connection.github_repository_id)
        sync_session.add(
            GitHubArtifact(
                repository_connection_id=connection.id,
                kind=GitHubArtifactKind.PULL_REQUEST,
                external_id=f"pr-{connection.github_repository_id}",
                number=17,
                title="PAY-1",
                html_url=f"{connection.html_url}/pull/17",
                author_login="sam",
                state=GitHubArtifactState.OPEN,
                ci_state=GitHubCIState.NONE,
                occurred_at=utcnow(),
            )
        )
        if connection.github_repository_id == 501:
            raise httpx.ConnectError("repository unavailable")

    monkeypatch.setattr(web_sprints, "sync_open_pull_requests", fake_sync, raising=False)
    login_as(settings_world.owner.email)
    response = client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/sync",
        data=_csrf(settings_world.owner),
    )

    assert response.status_code == 200
    assert seen == [501, 502]
    assert "Retry sync" in response.text
    with Session(engine) as observer:
        repositories = observer.exec(
            select(ProjectGitHubRepository).order_by(
                ProjectGitHubRepository.github_repository_id
            )
        ).all()
        assert [row.active for row in repositories] == [True, True, False]
        artifacts = observer.exec(select(GitHubArtifact)).all()
        assert [artifact.external_id for artifact in artifacts] == ["pr-502"]


def test_github_attention_and_owner_disconnected_states_are_distinct(
    client, settings_world, session, login_as, connected_github
):
    connected_github.active = False
    connected_github.disconnected_at = None
    session.add(connected_github)
    session.commit()
    login_as(settings_world.owner.email)

    attention = client.get(f"/projects/{settings_world.project.slug}/settings")

    assert "Connection needs attention" in attention.text
    connected_github.disconnected_at = utcnow()
    session.add(connected_github)
    session.commit()
    disconnected = client.get(f"/projects/{settings_world.project.slug}/settings")
    assert "Disconnected" in disconnected.text


def test_github_attention_card_shows_member_status_and_names_without_controls_or_setup(
    client, settings_world, login_as, connected_github
):
    login_as(settings_world.member.email)

    page = client.get(f"/projects/{settings_world.project.slug}/settings")

    assert "Connected" in page.text
    assert "acme/api" in page.text
    assert "Connect GitHub" not in page.text
    assert "Save repositories" not in page.text
    assert "Disconnect GitHub" not in page.text
    assert "GitHub App settings not configured" not in page.text
    assert 'settings/integrations/github' not in page.text
    assert "acme-inc" not in page.text
    assert "https://github.com/acme/api" not in page.text
    assert "7001" not in page.text and "501" not in page.text


def test_github_attention_card_shows_owner_account_and_connection_controls(
    client, settings_world, login_as, connected_github, configured_github
):
    login_as(settings_world.owner.email)

    page = client.get(f"/projects/{settings_world.project.slug}/settings")

    assert "Connected" in page.text
    assert "acme-inc" in page.text and "acme/api" in page.text
    assert (
        f'action="/projects/{settings_world.project.slug}/settings/integrations/github/connect"'
        in page.text
    )
    assert (
        f'action="/projects/{settings_world.project.slug}/settings/integrations/github/disconnect"'
        in page.text
    )
    assert (
        f'action="/projects/{settings_world.project.slug}/settings/integrations/github/sync"'
        in page.text
    )
    assert "Retry sync" in page.text


def test_github_attention_page_maps_error_codes_without_echoing_raw_values(
    client, settings_world, login_as
):
    login_as(settings_world.owner.email)

    denied = client.get(
        f"/projects/{settings_world.project.slug}/settings?github_error=oauth_denied"
    )
    unknown = client.get(
        f"/projects/{settings_world.project.slug}/settings?github_error=raw-secret"
    )

    assert "GitHub authorization was denied." in denied.text
    assert "raw-secret" not in unknown.text


def test_settings_template_context_contains_only_chat_provider_status(
    client, settings_world, session, login_as, monkeypatch
):
    project = session.get(Project, settings_world.project.id)
    secret = "https://hooks.example.test/context-secret"
    set_chat_webhook(session, project, WebhookType.SLACK, secret)
    captured = {}

    def capture_render(request, name, context, **kwargs):
        captured.update(context)
        return Response()

    monkeypatch.setattr(web_sprints, "render", capture_render)
    login_as(settings_world.owner.email)

    response = client.get(f"/projects/{settings_world.project.slug}/settings")

    assert response.status_code == 200
    assert captured["chat_integrations"] == {"SLACK": True}
    assert secret not in repr(captured["chat_integrations"])


def test_owner_renames_project_without_changing_its_slug(client, settings_world, engine, login_as):
    login_as(settings_world.owner.email)

    response = client.post(
        f"/projects/{settings_world.project.slug}/settings/project",
        data={"name": "Checkout", **_csrf(settings_world.owner)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/projects/{settings_world.project.slug}/settings?saved=1"
    )
    with Session(engine) as session:
        project = session.get(Project, settings_world.project.id)
    assert (project.name, project.slug) == ("Checkout", "payment-gateway")


def test_chat_webhook_validation_re_renders_settings_without_url(
    client, settings_world, login_as
):
    login_as(settings_world.owner.email)

    response = client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/SLACK",
        data={
            "url": "https://",
            **_csrf(settings_world.owner),
        },
    )

    assert response.status_code == 422
    assert "safe https URL" in response.text
    assert 'value="https://"' not in response.text


def test_chat_integration_routes_are_owner_only_and_never_render_secret(
    client, settings_world, session, login_as
):
    secret = "https://hooks.example.test/secret"
    project = session.get(Project, settings_world.project.id)
    set_chat_webhook(session, project, WebhookType.SLACK, secret)
    member_url = f"/projects/{settings_world.project.slug}/settings/integrations/SLACK"
    disconnect_url = f"{member_url}/disconnect"

    login_as(settings_world.member.email)
    assert client.post(
        member_url,
        data={"url": "https://hooks.example.test/x", **_csrf(settings_world.member)},
    ).status_code == 403
    member_page = client.get(f"/projects/{settings_world.project.slug}/settings")
    assert "Slack" in member_page.text
    assert "Connected" in member_page.text
    assert 'name="url"' not in member_page.text

    client.post("/api/v1/auth/logout")
    login_as(settings_world.owner.email)
    owner_project = client.get(f"/api/v1/projects/{settings_world.project.slug}")
    owner_page = client.get(f"/projects/{settings_world.project.slug}/settings")

    assert "webhook_type" not in owner_project.json()
    assert "webhook_url" not in owner_project.json()
    assert "Configured" in owner_page.text
    assert 'name="url"' in owner_page.text
    assert secret not in owner_page.text
    assert client.post(
        disconnect_url,
        data={"confirm": "Disconnect", **_csrf(settings_world.owner)},
        follow_redirects=False,
    ).status_code == 303
    assert chat_webhooks(session, settings_world.project.id) == []


def test_chat_integration_connects_and_rejects_invalid_provider_and_confirmation(
    client, settings_world, session, login_as
):
    base = f"/projects/{settings_world.project.slug}/settings/integrations"
    login_as(settings_world.owner.email)

    connected = client.post(
        f"{base}/TEAMS",
        data={"url": "https://hooks.example.test/teams", **_csrf(settings_world.owner)},
        follow_redirects=False,
    )
    lowercase = client.post(
        f"{base}/teams",
        data={"url": "https://hooks.example.test/teams", **_csrf(settings_world.owner)},
    )
    wrong_confirm = client.post(
        f"{base}/TEAMS/disconnect",
        data={"confirm": "yes", **_csrf(settings_world.owner)},
    )

    assert connected.status_code == 303
    assert [row.provider for row in chat_webhooks(session, settings_world.project.id)] == [
        WebhookType.TEAMS
    ]
    assert lowercase.status_code == 404
    assert wrong_confirm.status_code == 422


def test_owner_adds_changes_and_removes_members(client, settings_world, engine, login_as):
    login_as(settings_world.owner.email)

    added = client.post(
        f"/projects/{settings_world.project.slug}/settings/members",
        data={
            "email": settings_world.candidate.email,
            "role": "MEMBER",
            **_csrf(settings_world.owner),
        },
        follow_redirects=False,
    )
    changed = client.post(
        f"/projects/{settings_world.project.slug}/settings/members/{settings_world.member.id}",
        data={"role": "OWNER", **_csrf(settings_world.owner)},
        follow_redirects=False,
    )
    removed = client.post(
        f"/projects/{settings_world.project.slug}/settings/members/{settings_world.candidate.id}/remove",
        data=_csrf(settings_world.owner),
        follow_redirects=False,
    )

    assert (added.status_code, changed.status_code, removed.status_code) == (303, 303, 303)
    with Session(engine) as session:
        rows = session.exec(
            select(ProjectMember).where(ProjectMember.project_id == settings_world.project.id)
        ).all()
    assert {(row.user_id, row.role) for row in rows} == {
        (settings_world.owner.id, Role.OWNER),
        (settings_world.member.id, Role.OWNER),
    }


def test_settings_preserves_last_owner_and_requires_csrf(client, settings_world, login_as):
    login_as(settings_world.owner.email)

    missing_csrf = client.post(
        f"/projects/{settings_world.project.slug}/settings/members/{settings_world.owner.id}",
        data={"role": "MEMBER"},
    )
    last_owner = client.post(
        f"/projects/{settings_world.project.slug}/settings/members/{settings_world.owner.id}",
        data={"role": "MEMBER", **_csrf(settings_world.owner)},
    )

    assert missing_csrf.status_code == 403
    assert last_owner.status_code == 409
    assert "at least one OWNER" in last_owner.text


def test_project_delete_requires_immutable_slug_confirmation(
    client, settings_world, engine, login_as
):
    login_as(settings_world.owner.email)

    denied = client.post(
        f"/projects/{settings_world.project.slug}/settings/delete",
        data={"confirm": "Payment Gateway", **_csrf(settings_world.owner)},
    )
    deleted = client.post(
        f"/projects/{settings_world.project.slug}/settings/delete",
        data={"confirm": settings_world.project.slug, **_csrf(settings_world.owner)},
        follow_redirects=False,
    )

    assert denied.status_code == 422
    assert "confirm must equal the slug" in denied.text
    assert deleted.status_code == 303
    with Session(engine) as session:
        assert session.get(Project, settings_world.project.id) is None
