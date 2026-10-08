import hashlib
import hmac
import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.config import settings
from app.github_sync import (
    claim_github_delivery,
    process_github_delivery,
    upsert_pull_request,
)
from app.models import (
    GitHubArtifact,
    GitHubArtifactKind,
    GitHubCIState,
    GitHubInstallation,
    GitHubReviewState,
    IntegrationDelivery,
    ProjectGitHubConnection,
    ProjectGitHubRepository,
    Ticket,
    TicketGitLink,
    TicketStatus,
)
from app.services import create_ticket

SECRET = "webhook-test-secret"


class FakeGitHub:
    def __init__(self):
        self.reviews = []
        self.pull_requests = []
        self.checks = []
        self.review_error = None
        self.pull_error = None
        self.check_error = None
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def pull_request_reviews(self, installation_id, full_name, number):
        self.calls.append(("reviews", installation_id, full_name, number))
        if self.review_error:
            raise self.review_error
        return self.reviews

    def pull_requests_for_commit(self, installation_id, full_name, sha):
        self.calls.append(("pulls", installation_id, full_name, sha))
        if self.pull_error:
            raise self.pull_error
        return self.pull_requests

    def check_runs(self, installation_id, full_name, sha):
        self.calls.append(("checks", installation_id, full_name, sha))
        if self.check_error:
            raise self.check_error
        return self.checks


def _headers(raw: bytes, event: str = "ping", delivery: str = "delivery-1") -> dict[str, str]:
    signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-GitHub-Delivery": delivery,
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": f"sha256={signature}",
    }


def _post(client, event: str, payload: dict, delivery: str = "delivery-1"):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return client.post(
        "/integrations/github/webhook",
        content=raw,
        headers=_headers(raw, event, delivery),
    )


def _pull(project_key: str, *, title: str | None = None) -> dict:
    return {
        "id": 9001,
        "node_id": "PR_node_9001",
        "number": 17,
        "title": title or f"Build {project_key}-1",
        "body": f"Also handles {project_key}-2",
        "draft": False,
        "state": "open",
        "merged_at": None,
        "html_url": "https://github.com/acme/api/pull/17",
        "user": {"login": "octocat"},
        "head": {"ref": f"feature/{project_key.lower()}-1", "sha": "a" * 40},
        "updated_at": "2026-09-17T12:00:00Z",
    }


@pytest.fixture
def signed_webhook(client, engine, monkeypatch):
    from app.routers import github_webhook

    monkeypatch.setattr(settings, "github_webhook_secret", SECRET)
    monkeypatch.setattr(github_webhook, "get_engine", lambda: engine)
    github = FakeGitHub()
    monkeypatch.setattr(github_webhook, "GitHubClient", lambda: github)
    deliveries = iter(range(1000))

    def send(event: str, payload: dict, delivery: str | None = None):
        return _post(client, event, payload, delivery or f"delivery-{next(deliveries)}")

    send.github = github
    return send


@pytest.fixture
def connected_repo(session, make_user, make_project):
    owner = make_user(email="webhook-owner@example.com")
    project = make_project(owner, name="Payment Gateway")
    installation = GitHubInstallation(
        github_installation_id=7001,
        account_id=91,
        account_login="acme",
        connected_by_id=owner.id,
    )
    session.add(installation)
    session.flush()
    session.add(
        ProjectGitHubConnection(project_id=project.id, installation_id=installation.id)
    )
    connection = ProjectGitHubRepository(
        project_id=project.id,
        installation_id=installation.id,
        github_repository_id=501,
        full_name="acme/api",
        html_url="https://github.com/acme/api",
        default_branch="main",
    )
    session.add(connection)
    session.commit()
    first = create_ticket(session, project, owner, title="First")
    second = create_ticket(session, project, owner, title="Second")
    return SimpleNamespace(
        owner=owner,
        project=project,
        installation=installation,
        connection=connection,
        first=first,
        second=second,
    )


def test_missing_webhook_config_returns_503_without_login(client, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", None)

    response = client.post("/integrations/github/webhook", content=b"{}")

    assert response.status_code == 503


@pytest.mark.parametrize(
    "signature",
    [None, "malformed", "sha1=wrong", "sha256=" + "0" * 64],
)
def test_invalid_signature_is_rejected(signature, client, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", SECRET)
    headers = {"X-Hub-Signature-256": signature} if signature else {}

    response = client.post(
        "/integrations/github/webhook",
        content=b"not-json",
        headers=headers,
    )

    assert response.status_code == 401


def test_signature_failure_precedes_json_parse_and_database_session(client, monkeypatch):
    from app.routers import github_webhook

    monkeypatch.setattr(settings, "github_webhook_secret", SECRET)
    calls = []

    def explode(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("called before signature validation")

    monkeypatch.setattr(github_webhook.json, "loads", explode)
    monkeypatch.setattr(github_webhook, "Session", explode)

    response = client.post(
        "/integrations/github/webhook",
        content=b"not-json",
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
    )

    assert response.status_code == 401
    assert calls == []


def test_malformed_signed_json_returns_400_before_database_session(client, monkeypatch):
    from app.routers import github_webhook

    monkeypatch.setattr(settings, "github_webhook_secret", SECRET)
    monkeypatch.setattr(
        github_webhook,
        "Session",
        lambda *_args, **_kwargs: pytest.fail("database opened before JSON parsing"),
    )
    raw = b"not-json"

    response = client.post(
        "/integrations/github/webhook",
        content=raw,
        headers=_headers(raw),
    )

    assert response.status_code == 400


def test_duplicate_delivery_is_recorded_once(client, engine, session, monkeypatch):
    from app.routers import github_webhook

    monkeypatch.setattr(settings, "github_webhook_secret", SECRET)
    monkeypatch.setattr(github_webhook, "get_engine", lambda: engine)

    first = _post(client, "ping", {}, "same-delivery")
    second = _post(client, "ping", {}, "same-delivery")

    assert first.status_code == second.status_code == 200
    rows = session.exec(
        select(IntegrationDelivery).where(
            IntegrationDelivery.provider == "GITHUB",
            IntegrationDelivery.delivery_id == "same-delivery",
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].event_type == "ping"


@pytest.mark.parametrize(
    "action",
    [
        "opened",
        "edited",
        "synchronize",
        "reopened",
        "ready_for_review",
        "converted_to_draft",
        "closed",
    ],
)
def test_pull_request_actions_refresh_artifact(action, signed_webhook, connected_repo, session):
    pull = _pull(connected_repo.project.key)

    response = signed_webhook(
        "pull_request",
        {
            "action": action,
            "installation": {"id": 7001},
            "repository": {"id": 501},
            "pull_request": pull,
        },
    )

    assert response.status_code == 200
    artifact = session.exec(select(GitHubArtifact)).one()
    assert artifact.title == pull["title"]
    assert artifact.head_sha == "a" * 40
    ticket_numbers = session.exec(
        select(Ticket.ticket_number)
        .join(TicketGitLink, TicketGitLink.ticket_id == Ticket.id)
        .where(TicketGitLink.artifact_id == artifact.id)
        .order_by(Ticket.ticket_number)
    ).all()
    assert ticket_numbers == [1, 2]


@pytest.mark.parametrize("action", ["assigned", "unassigned", "labeled", "unlabeled"])
def test_unrelated_pull_request_actions_record_only(
    action, signed_webhook, connected_repo, session
):
    response = signed_webhook(
        "pull_request",
        {
            "action": action,
            "installation": {"id": 7001},
            "repository": {"id": 501},
            "pull_request": _pull(connected_repo.project.key),
        },
    )

    assert response.status_code == 200
    assert session.exec(select(GitHubArtifact)).all() == []
    assert len(session.exec(select(IntegrationDelivery)).all()) == 1


def test_repository_and_installation_lookup_updates_each_matching_project(
    signed_webhook, connected_repo, session, make_project
):
    second_project = make_project(connected_repo.owner, name="Payment Processor")
    session.add(
        ProjectGitHubConnection(
            project_id=second_project.id,
            installation_id=connected_repo.installation.id,
        )
    )
    second_connection = ProjectGitHubRepository(
        project_id=second_project.id,
        installation_id=connected_repo.installation.id,
        github_repository_id=501,
        full_name="acme/api",
        html_url="https://github.com/acme/api",
        default_branch="main",
    )
    session.add(second_connection)
    session.commit()
    second_ticket = create_ticket(session, second_project, connected_repo.owner, title="Other")
    title = f"{connected_repo.project.key}-1 and {second_project.key}-1"
    pull = _pull(connected_repo.project.key, title=title)
    pull["body"] = ""
    pull["head"]["ref"] = "feature/no-ticket"

    response = signed_webhook(
        "pull_request",
        {
            "action": "opened",
            "installation": {"id": 7001},
            "repository": {"id": 501},
            "pull_request": pull,
        },
    )

    assert response.status_code == 200
    artifacts = session.exec(select(GitHubArtifact)).all()
    assert {artifact.repository_connection_id for artifact in artifacts} == {
        connected_repo.connection.id,
        second_connection.id,
    }
    linked_ticket_ids = set(session.exec(select(TicketGitLink.ticket_id)).all())
    assert linked_ticket_ids == {connected_repo.first.id, second_ticket.id}


@pytest.mark.parametrize(
    ("repository", "installation"),
    [
        ({}, {"id": 7001}),
        ({"id": "501"}, {"id": 7001}),
        ({"id": 501}, {}),
        ({"id": 501}, {"id": True}),
    ],
)
def test_malformed_repository_and_installation_lookup_records_only(
    repository, installation, signed_webhook, connected_repo, session
):
    response = signed_webhook(
        "pull_request",
        {
            "action": "opened",
            "installation": installation,
            "repository": repository,
            "pull_request": _pull(connected_repo.project.key),
        },
    )

    assert response.status_code == 200
    assert session.exec(select(GitHubArtifact)).all() == []
    assert len(session.exec(select(IntegrationDelivery)).all()) == 1


def _stored_pull(session, connected_repo):
    artifact = upsert_pull_request(
        session,
        connected_repo.connection,
        _pull(connected_repo.project.key),
    )
    session.commit()
    return artifact


@pytest.mark.parametrize("action", ["submitted", "edited", "dismissed"])
def test_review_actions_refresh_existing_pull_request(
    action, signed_webhook, connected_repo, session
):
    artifact = _stored_pull(session, connected_repo)
    signed_webhook.github.reviews = [
        {
            "id": 81,
            "state": "APPROVED",
            "submitted_at": "2026-09-17T13:00:00Z",
            "user": {"id": 7, "login": "reviewer"},
        }
    ]

    response = signed_webhook(
        "pull_request_review",
        {
            "action": action,
            "installation": {"id": 7001},
            "repository": {"id": 501},
            "pull_request": {"number": 17},
        },
    )

    assert response.status_code == 200
    session.refresh(artifact)
    assert artifact.review_state is GitHubReviewState.APPROVED
    assert signed_webhook.github.calls == [("reviews", 7001, "acme/api", 17)]


@pytest.mark.parametrize("action", ["created", "rerequested", "completed", "requested_action"])
def test_check_run_actions_refresh_only_returned_existing_pull_requests(
    action, signed_webhook, connected_repo, session
):
    artifact = _stored_pull(session, connected_repo)
    other_pull = _pull(connected_repo.project.key)
    other_pull.update({"id": 9002, "node_id": "PR_node_9002", "number": 18})
    other = upsert_pull_request(session, connected_repo.connection, other_pull)
    session.commit()
    signed_webhook.github.pull_requests = [_pull(connected_repo.project.key)]
    signed_webhook.github.checks = [
        {
            "id": 71,
            "name": "test",
            "status": "completed",
            "conclusion": "success",
            "head_sha": "a" * 40,
        }
    ]

    response = signed_webhook(
        "check_run",
        {
            "action": action,
            "installation": {"id": 7001},
            "repository": {"id": 501},
            "check_run": {"head_sha": "a" * 40},
        },
    )

    assert response.status_code == 200
    session.refresh(artifact)
    session.refresh(other)
    assert artifact.ci_state is GitHubCIState.PASSED
    assert other.ci_state is GitHubCIState.NONE
    assert signed_webhook.github.calls == [
        ("pulls", 7001, "acme/api", "a" * 40),
        ("checks", 7001, "acme/api", "a" * 40),
    ]


def test_check_run_for_stale_sha_commits_delivery_without_changing_current_ci(
    signed_webhook, connected_repo, session
):
    artifact = _stored_pull(session, connected_repo)
    pull = _pull(connected_repo.project.key)
    pull["head"]["sha"] = "b" * 40
    artifact = upsert_pull_request(session, connected_repo.connection, pull)
    artifact.ci_state = GitHubCIState.PENDING
    session.add(artifact)
    session.commit()
    signed_webhook.github.pull_requests = [_pull(connected_repo.project.key)]
    signed_webhook.github.checks = [
        {"status": "completed", "conclusion": "success", "head_sha": "a" * 40}
    ]

    response = signed_webhook(
        "check_run",
        {
            "action": "completed",
            "installation": {"id": 7001},
            "repository": {"id": 501},
            "check_run": {"head_sha": "a" * 40},
        },
        "stale-check",
    )

    assert response.status_code == 200
    session.refresh(artifact)
    assert artifact.head_sha == "b" * 40
    assert artifact.ci_state is GitHubCIState.PENDING
    assert session.exec(
        select(IntegrationDelivery).where(IntegrationDelivery.delivery_id == "stale-check")
    ).one()


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        ("pull_request_review", {"action": "submitted", "pull_request": {"number": 17}}),
        ("check_run", {"action": "completed", "check_run": {"head_sha": "a" * 40}}),
    ],
)
def test_aggregate_refresh_failure_preserves_state_and_commits_delivery(
    event, payload, signed_webhook, connected_repo, session, caplog
):
    artifact = _stored_pull(session, connected_repo)
    artifact.review_state = GitHubReviewState.CHANGES_REQUESTED
    artifact.ci_state = GitHubCIState.FAILED
    session.add(artifact)
    session.commit()
    error = httpx.ReadTimeout(
        "token=never-log-this response-body",
        request=httpx.Request("GET", "https://api.github.com/private"),
    )
    signed_webhook.github.pull_requests = [_pull(connected_repo.project.key)]
    if event == "pull_request_review":
        signed_webhook.github.review_error = error
    else:
        signed_webhook.github.check_error = error

    with caplog.at_level(logging.WARNING):
        response = signed_webhook(
            event,
            {
                **payload,
                "installation": {"id": 7001},
                "repository": {"id": 501},
            },
        )

    assert response.status_code == 200
    session.refresh(artifact)
    assert artifact.review_state is GitHubReviewState.CHANGES_REQUESTED
    assert artifact.ci_state is GitHubCIState.FAILED
    assert session.exec(
        select(IntegrationDelivery).where(IntegrationDelivery.event_type == event)
    ).one()
    assert "acme/api" in caplog.text
    assert "17" in caplog.text
    assert "never-log-this" not in caplog.text
    assert "response-body" not in caplog.text


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        ("pull_request_review", {"action": "unknown", "pull_request": {"number": 17}}),
        ("check_run", {"action": "unknown", "check_run": {"head_sha": "a" * 40}}),
    ],
)
def test_unknown_review_and_check_run_actions_record_only(
    event, payload, signed_webhook, connected_repo, session
):
    artifact = _stored_pull(session, connected_repo)

    response = signed_webhook(
        event,
        {
            **payload,
            "installation": {"id": 7001},
            "repository": {"id": 501},
        },
    )

    assert response.status_code == 200
    session.refresh(artifact)
    assert artifact.review_state is GitHubReviewState.REVIEW_REQUIRED
    assert artifact.ci_state is GitHubCIState.NONE
    assert signed_webhook.github.calls == []


def test_push_stores_only_linked_commits_with_sha_derived_url(
    signed_webhook, connected_repo, session
):
    connected_repo.first.status = TicketStatus.IN_PROGRESS
    session.add(connected_repo.first)
    session.commit()
    sha = "b" * 40

    response = signed_webhook(
        "push",
        {
            "installation": {"id": 7001},
            "repository": {"id": 501},
            "commits": [
                {
                    "id": sha,
                    "message": f"Ship {connected_repo.project.key}-1",
                    "timestamp": "2026-09-17T14:00:00Z",
                    "author": {"name": "Ada", "email": "ada@example.com", "username": "ada"},
                    "committer": {"name": "Ada", "email": "ada@example.com", "username": "ada"},
                    "url": "javascript:alert(1)",
                },
                {
                    "id": "c" * 40,
                    "message": "No ticket key",
                    "timestamp": "2026-09-17T14:01:00Z",
                    "author": {"name": "Ada", "email": "ada@example.com"},
                    "committer": {"name": "Ada", "email": "ada@example.com"},
                    "url": "https://evil.example/commit",
                },
            ],
        },
    )

    assert response.status_code == 200
    artifact = session.exec(
        select(GitHubArtifact).where(GitHubArtifact.kind == GitHubArtifactKind.COMMIT)
    ).one()
    assert artifact.external_id == sha
    assert artifact.html_url == f"https://github.com/acme/api/commit/{sha}"
    session.refresh(connected_repo.first)
    assert connected_repo.first.status is TicketStatus.IN_PROGRESS


def test_push_ignores_oversized_reference_and_commits_delivery_and_valid_commit(
    signed_webhook, connected_repo, session
):
    valid_sha = "d" * 40

    response = signed_webhook(
        "push",
        {
            "installation": {"id": 7001},
            "repository": {"id": 501},
            "commits": [
                {
                    "id": "c" * 40,
                    "message": f"{connected_repo.project.key}-{'9' * 5000}",
                    "timestamp": "2026-09-17T14:00:00Z",
                },
                {
                    "id": valid_sha,
                    "message": f"Ship {connected_repo.project.key}-1",
                    "timestamp": "2026-09-17T14:01:00Z",
                },
            ],
        },
        "oversized-reference",
    )

    assert response.status_code == 200
    assert session.exec(
        select(IntegrationDelivery).where(IntegrationDelivery.delivery_id == "oversized-reference")
    ).one()
    artifact = session.exec(select(GitHubArtifact)).one()
    assert artifact.external_id == valid_sha
    assert session.get(TicketGitLink, (connected_repo.first.id, artifact.id)) is not None


def test_delete_linked_ticket_preserves_github_artifact_and_other_ticket(
    client, connected_repo, login_as, session
):
    artifact = _stored_pull(session, connected_repo)
    first_id = connected_repo.first.id
    second_id = connected_repo.second.id
    artifact_id = artifact.id
    session.add_all(
        [
            TicketGitLink(ticket_id=first_id, artifact_id=artifact_id),
            TicketGitLink(ticket_id=second_id, artifact_id=artifact_id),
        ]
    )
    session.commit()
    login_as(connected_repo.owner.email)

    try:
        response = client.delete(f"/api/v1/tickets/{first_id}")
    except IntegrityError as exc:
        pytest.fail(f"linked ticket deletion raised IntegrityError: {exc}")

    assert response.status_code == 204, response.text
    session.expire_all()
    assert session.get(Ticket, first_id) is None
    assert session.get(Ticket, second_id) is not None
    assert session.get(GitHubArtifact, artifact_id) is not None
    assert session.get(TicketGitLink, (first_id, artifact_id)) is None
    assert session.get(TicketGitLink, (second_id, artifact_id)) is not None


def test_installation_repositories_removed_marks_only_listed_repository_attention_required(
    signed_webhook, connected_repo, session
):
    other = ProjectGitHubRepository(
        project_id=connected_repo.project.id,
        installation_id=connected_repo.installation.id,
        github_repository_id=502,
        full_name="acme/web",
        html_url="https://github.com/acme/web",
        default_branch="main",
    )
    session.add(other)
    session.commit()

    response = signed_webhook(
        "installation_repositories",
        {
            "action": "removed",
            "installation": {"id": 7001},
            "repositories_removed": [{"id": 501, "full_name": "acme/api"}],
        },
    )

    assert response.status_code == 200
    session.refresh(connected_repo.connection)
    session.refresh(other)
    assert connected_repo.connection.active is False
    assert connected_repo.connection.disconnected_at is None
    assert other.active is True


def test_installation_repositories_added_does_not_reactivate_selection(
    signed_webhook, connected_repo, session
):
    connected_repo.connection.active = False
    connected_repo.connection.disconnected_at = None
    session.add(connected_repo.connection)
    session.commit()

    response = signed_webhook(
        "installation_repositories",
        {
            "action": "added",
            "installation": {"id": 7001},
            "repositories_added": [{"id": 501, "full_name": "acme/api"}],
        },
    )

    assert response.status_code == 200
    session.refresh(connected_repo.connection)
    assert connected_repo.connection.active is False
    assert connected_repo.connection.disconnected_at is None


@pytest.mark.parametrize("action", ["deleted", "suspend"])
def test_installation_lifecycle_disable_marks_all_repositories_attention_required(
    action, signed_webhook, connected_repo, session
):
    other = ProjectGitHubRepository(
        project_id=connected_repo.project.id,
        installation_id=connected_repo.installation.id,
        github_repository_id=502,
        full_name="acme/web",
        html_url="https://github.com/acme/web",
        default_branch="main",
    )
    session.add(other)
    session.commit()

    response = signed_webhook(
        "installation",
        {"action": action, "installation": {"id": 7001}},
    )

    assert response.status_code == 200
    session.refresh(connected_repo.connection)
    session.refresh(other)
    assert connected_repo.connection.active is other.active is False
    assert connected_repo.connection.disconnected_at is other.disconnected_at is None


@pytest.mark.parametrize("action", ["unsuspend", "created", "new_permissions"])
def test_installation_lifecycle_non_disable_actions_record_only(
    action, signed_webhook, connected_repo, session
):
    if action == "unsuspend":
        connected_repo.connection.active = False
        connected_repo.connection.disconnected_at = None
        session.add(connected_repo.connection)
        session.commit()

    response = signed_webhook(
        "installation",
        {"action": action, "installation": {"id": 7001}},
    )

    assert response.status_code == 200
    session.refresh(connected_repo.connection)
    assert connected_repo.connection.active is (action != "unsuspend")
    assert len(session.exec(select(IntegrationDelivery)).all()) == 1


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        (
            "unknown_event",
            {
                "installation": {"id": 7001},
                "repository": {"id": 501},
                "commits": [{"id": "d" * 40, "message": "PAY-1"}],
            },
        ),
        ("installation", {"action": "unknown", "installation": {"id": 7001}}),
    ],
)
def test_unknown_event_or_action_records_only(
    event, payload, signed_webhook, connected_repo, session
):
    response = signed_webhook(event, payload)

    assert response.status_code == 200
    session.refresh(connected_repo.connection)
    assert connected_repo.connection.active is True
    assert session.exec(select(GitHubArtifact)).all() == []
    assert len(session.exec(select(IntegrationDelivery)).all()) == 1


@pytest.mark.parametrize(
    ("event", "event_object"),
    [
        ("pull_request", {"pull_request": _pull("PAY")}),
        ("pull_request_review", {"pull_request": {"number": 17}}),
        ("check_run", {"check_run": {"head_sha": "a" * 40}}),
        ("installation", {}),
    ],
)
def test_malformed_action_type_records_only(
    event, event_object, signed_webhook, connected_repo, session
):
    response = signed_webhook(
        event,
        {
            "action": [],
            "installation": {"id": 7001},
            "repository": {"id": 501},
            **event_object,
        },
    )

    assert response.status_code == 200
    assert len(session.exec(select(IntegrationDelivery)).all()) == 1
    assert session.exec(select(GitHubArtifact)).all() == []
    session.refresh(connected_repo.connection)
    assert connected_repo.connection.active is True
    assert signed_webhook.github.calls == []


@pytest.mark.parametrize(
    "event_object",
    [{}, {"pull_request": []}],
    ids=["missing", "wrong-shaped"],
)
def test_malformed_pull_request_object_records_only(
    event_object, signed_webhook, connected_repo, session
):
    response = signed_webhook(
        "pull_request",
        {
            "action": "opened",
            "installation": {"id": 7001},
            "repository": {"id": 501},
            **event_object,
        },
    )

    assert response.status_code == 200
    assert len(session.exec(select(IntegrationDelivery)).all()) == 1
    assert session.exec(select(GitHubArtifact)).all() == []


def test_delivery_unique_conflict_is_treated_as_duplicate(session):
    session.add(
        IntegrationDelivery(
            provider="GITHUB",
            delivery_id="race-delivery",
            event_type="push",
        )
    )
    session.commit()
    loser = IntegrationDelivery(
        provider="GITHUB",
        delivery_id="race-delivery",
        event_type="push",
    )

    assert claim_github_delivery(session, loser) is False
    assert len(
        session.exec(
            select(IntegrationDelivery).where(
                IntegrationDelivery.provider == "GITHUB",
                IntegrationDelivery.delivery_id == "race-delivery",
            )
        ).all()
    ) == 1


def test_delivery_unrelated_integrity_error_is_not_treated_as_duplicate(session):
    session.add(
        IntegrationDelivery(
            id="fixed-id",
            provider="GITHUB",
            delivery_id="first-delivery",
            event_type="push",
        )
    )
    session.commit()
    unrelated = IntegrationDelivery(
        id="fixed-id",
        provider="GITHUB",
        delivery_id="second-delivery",
        event_type="push",
    )

    with pytest.raises(IntegrityError):
        claim_github_delivery(session, unrelated)


def test_delivery_identity_is_provider_scoped(session):
    session.add(
        IntegrationDelivery(
            provider="SLACK",
            delivery_id="shared-delivery",
            event_type="message",
        )
    )
    session.commit()

    assert process_github_delivery(
        session,
        "shared-delivery",
        "ping",
        {},
        FakeGitHub(),
    )
    assert set(
        session.exec(
            select(IntegrationDelivery.provider).where(
                IntegrationDelivery.delivery_id == "shared-delivery"
            )
        ).all()
    ) == {"GITHUB", "SLACK"}


def test_unexpected_processing_error_rolls_back_delivery(
    session, connected_repo, monkeypatch
):
    import app.github_sync as github_sync

    def explode(_session, _connection, _commit):
        raise RuntimeError("boom")

    monkeypatch.setattr(github_sync, "insert_linked_commit", explode)

    with pytest.raises(RuntimeError, match="boom"):
        process_github_delivery(
            session,
            "retryable-delivery",
            "push",
            {
                "repository": {"id": 501},
                "installation": {"id": 7001},
                "commits": [
                    {
                        "id": "a" * 40,
                        "message": f"{connected_repo.project.key}-1",
                        "timestamp": "2026-09-17T15:00:00Z",
                    }
                ],
            },
            FakeGitHub(),
        )

    assert session.exec(
        select(IntegrationDelivery).where(
            IntegrationDelivery.delivery_id == "retryable-delivery"
        )
    ).first() is None
    assert session.exec(select(GitHubArtifact)).all() == []


def _send_pull(signed_webhook, project, action, **changes):
    pull = {**_pull(project.key), **changes}
    return signed_webhook(
        "pull_request",
        {
            "action": action,
            "installation": {"id": 7001},
            "repository": {"id": 501},
            "pull_request": pull,
        },
    )


@pytest.mark.parametrize("action", ["opened", "reopened", "ready_for_review"])
def test_opened_pull_request_starts_backlog_and_selected_tickets_only(
    action, signed_webhook, connected_repo, session
):
    connected_repo.second.status = TicketStatus.DONE
    session.add(connected_repo.second)
    session.commit()

    assert _send_pull(signed_webhook, connected_repo.project, action).status_code == 200

    session.refresh(connected_repo.first)
    session.refresh(connected_repo.second)
    assert connected_repo.first.status is TicketStatus.IN_PROGRESS
    assert connected_repo.second.status is TicketStatus.DONE


def test_pull_request_edit_never_moves_a_ticket_back_to_in_progress(
    signed_webhook, connected_repo, session
):
    assert connected_repo.first.status is TicketStatus.BACKLOG

    _send_pull(signed_webhook, connected_repo.project, "synchronize")

    session.refresh(connected_repo.first)
    assert connected_repo.first.status is TicketStatus.BACKLOG


def test_merged_pull_request_finishes_every_mentioned_ticket(
    signed_webhook, connected_repo, session
):
    connected_repo.first.status = TicketStatus.IN_PROGRESS
    session.add(connected_repo.first)
    session.commit()

    _send_pull(
        signed_webhook,
        connected_repo.project,
        "closed",
        state="closed",
        merged_at="2026-09-17T13:00:00Z",
    )

    for ticket in (connected_repo.first, connected_repo.second):
        session.refresh(ticket)
        assert ticket.status is TicketStatus.DONE
        assert ticket.completed_at is not None


def test_pull_request_closed_without_merge_leaves_tickets_alone(
    signed_webhook, connected_repo, session
):
    _send_pull(signed_webhook, connected_repo.project, "closed", state="closed")

    session.refresh(connected_repo.first)
    assert connected_repo.first.status is TicketStatus.BACKLOG
