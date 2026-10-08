import re
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

import app.github_sync as github_sync
from app.auth import make_csrf_token
from app.models import (
    GitHubArtifact,
    GitHubArtifactKind,
    GitHubArtifactState,
    GitHubCIState,
    GitHubInstallation,
    GitHubReviewState,
    ProjectGitHubConnection,
    ProjectGitHubRepository,
    Ticket,
    TicketGitLink,
)


@pytest.fixture
def development_world(engine, make_user, make_project, add_member):
    owner = make_user(email="owner@example.com", name="Owner")
    member = make_user(email="member@example.com", name="Member")
    project = make_project(owner, name="Payment Gateway")
    add_member(project, member)
    with Session(engine) as session:
        ticket = Ticket(
            ticket_number=104,
            project_id=project.id,
            title="Fix timeout",
            creator_id=owner.id,
        )
        installation = GitHubInstallation(
            github_installation_id=7001,
            account_id=91,
            account_login="acme",
            connected_by_id=owner.id,
        )
        session.add_all([ticket, installation])
        session.flush()
        session.add(
            ProjectGitHubConnection(
                project_id=project.id,
                installation_id=installation.id,
            )
        )
        active = ProjectGitHubRepository(
            project_id=project.id,
            installation_id=installation.id,
            github_repository_id=501,
            full_name="acme/api",
            html_url="https://github.com/acme/api",
            default_branch="main",
        )
        unsafe = ProjectGitHubRepository(
            project_id=project.id,
            installation_id=installation.id,
            github_repository_id=502,
            full_name="acme/<em>unsafe</em>",
            html_url="https://github.com/acme/unsafe",
            default_branch="main",
        )
        inactive = ProjectGitHubRepository(
            project_id=project.id,
            installation_id=installation.id,
            github_repository_id=503,
            full_name="acme/archived",
            html_url="https://github.com/acme/archived",
            default_branch="main",
            active=False,
        )
        session.add_all([active, unsafe, inactive])
        session.flush()
        artifacts = [
            GitHubArtifact(
                repository_connection_id=unsafe.id,
                kind=GitHubArtifactKind.PULL_REQUEST,
                external_id="unsafe-pr",
                number=18,
                title="<script>alert(1)</script>",
                html_url="javascript:alert(1)",
                author_login="<b>mallory</b>",
                state=GitHubArtifactState.OPEN,
                review_state=GitHubReviewState.REVIEW_REQUIRED,
                ci_state=GitHubCIState.PENDING,
                occurred_at=datetime(2026, 9, 17, 14, tzinfo=UTC),
            ),
            GitHubArtifact(
                repository_connection_id=active.id,
                kind=GitHubArtifactKind.COMMIT,
                external_id="a" * 40,
                title="PAY-104 fix timeout",
                html_url=f"https://github.com/acme/api/commit/{'a' * 40}",
                author_login="sam",
                occurred_at=datetime(2026, 9, 17, 13, tzinfo=UTC),
            ),
            GitHubArtifact(
                repository_connection_id=active.id,
                kind=GitHubArtifactKind.PULL_REQUEST,
                external_id="safe-pr",
                number=17,
                title="Fix timeout PR",
                html_url="https://github.com/acme/api/pull/17",
                author_login="octocat",
                state=GitHubArtifactState.OPEN,
                review_state=GitHubReviewState.APPROVED,
                ci_state=GitHubCIState.PASSED,
                occurred_at=datetime(2026, 9, 17, 12, tzinfo=UTC),
            ),
            GitHubArtifact(
                repository_connection_id=active.id,
                kind=GitHubArtifactKind.PULL_REQUEST,
                external_id="foreign-origin",
                number=16,
                title="Wrong origin PR",
                html_url="https://example.com/acme/api/pull/16",
                author_login="eve",
                state=GitHubArtifactState.OPEN,
                review_state=GitHubReviewState.CHANGES_REQUESTED,
                ci_state=GitHubCIState.FAILED,
                occurred_at=datetime(2026, 9, 17, 11, tzinfo=UTC),
            ),
            GitHubArtifact(
                repository_connection_id=inactive.id,
                kind=GitHubArtifactKind.PULL_REQUEST,
                external_id="inactive-pr",
                number=99,
                title="Archived hidden work",
                html_url="https://github.com/acme/archived/pull/99",
                author_login="archive-bot",
                state=GitHubArtifactState.CLOSED,
                ci_state=GitHubCIState.NONE,
                occurred_at=datetime(2026, 9, 17, 15, tzinfo=UTC),
            ),
        ]
        session.add_all(artifacts)
        session.flush()
        session.add_all(
            [TicketGitLink(ticket_id=ticket.id, artifact_id=artifact.id) for artifact in artifacts]
        )
        session.commit()
        return SimpleNamespace(
            owner=owner,
            member=member,
            project=project,
            ticket_id=ticket.id,
            inactive_artifact_id=artifacts[-1].id,
        )


@pytest.mark.parametrize("viewer", ["owner", "member"])
@pytest.mark.parametrize("headers", [{"HX-Request": "true"}, {}])
def test_ticket_detail_renders_safe_active_development_for_every_reader_and_mode(
    client, development_world, login_as, viewer, headers
):
    login_as(getattr(development_world, viewer).email)

    response = client.get(
        f"/projects/{development_world.project.slug}/tickets/104",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert "PAY-104" in response.text
    assert "git checkout -b feature/pay-104-fix-timeout" in response.text
    assert "Development" in response.text
    assert "acme/api" in response.text
    assert "Pull request #17" in response.text
    assert "Commit" in response.text
    assert "Approved" in response.text
    assert "Passed" in response.text
    assert '<span class="ticket-development-badge is-failed">CI Failed</span>' in response.text
    assert '<span class="ticket-development-state is-failed">Failed</span>' in response.text
    assert "octocat" in response.text
    assert '<time datetime="2026-09-17T12:00:00+00:00" data-local-time>' in response.text
    assert "Archived hidden work" not in response.text
    assert "acme/&lt;em&gt;unsafe&lt;/em&gt;" in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "&lt;b&gt;mallory&lt;/b&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert 'href="javascript:' not in response.text
    assert 'href="https://example.com/acme/api/pull/16"' not in response.text
    assert "Wrong origin PR" in response.text
    assert (
        '<a href="https://github.com/acme/api/pull/17" target="_blank" '
        'rel="noopener noreferrer">Fix timeout PR</a>'
    ) in response.text
    unsafe_title = "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert response.text.index(unsafe_title) < response.text.index("PAY-104 fix timeout")
    assert response.text.index("PAY-104 fix timeout") < response.text.index("Fix timeout PR")
    assert response.text.index("Development") < response.text.index('id="ticket-comments"')


def test_inactive_development_history_is_retained_but_not_returned(
    client, development_world, login_as, session
):
    login_as(development_world.owner.email)

    response = client.get(
        f"/projects/{development_world.project.slug}/tickets/104",
        headers={"HX-Request": "true"},
    )

    assert "Archived hidden work" not in response.text
    assert session.get(GitHubArtifact, development_world.inactive_artifact_id) is not None
    assert session.exec(
        select(TicketGitLink).where(
            TicketGitLink.artifact_id == development_world.inactive_artifact_id
        )
    ).one()


def test_shared_ticket_detail_context_survives_validation_and_comment_rerenders(
    client, development_world, login_as
):
    login_as(development_world.owner.email)
    ticket_validation = client.post(
        f"/projects/{development_world.project.slug}/tickets/104",
        data={
            "title": "Fix timeout",
            "description": "",
            "type": "TASK",
            "priority": "MEDIUM",
            "story_points": "4",
            "status": "BACKLOG",
            "_csrf": make_csrf_token(development_world.owner.id),
        },
        headers={"HX-Request": "true"},
    )
    comment_validation = client.post(
        f"/projects/{development_world.project.slug}/tickets/104/comments",
        data={"body": "  ", "_csrf": make_csrf_token(development_world.owner.id)},
    )

    assert ticket_validation.status_code == 422
    assert comment_validation.status_code == 422
    for response in (ticket_validation, comment_validation):
        assert "PAY-104" in response.text
        assert "Development" in response.text
        assert "Fix timeout PR" in response.text
        assert 'id="ticket-comments"' in response.text


def test_development_row_interface_is_frozen():
    row_type = getattr(github_sync, "TicketDevelopmentRow", None)

    assert row_type is not None
    assert row_type.__dataclass_params__.frozen
    row = row_type(
        repository_full_name="acme/api",
        kind=GitHubArtifactKind.COMMIT,
        number=None,
        title="Fix",
        html_url=None,
        author_login="sam",
        state=None,
        review_state=None,
        ci_state=GitHubCIState.NONE,
        occurred_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    with pytest.raises(FrozenInstanceError):
        row.title = "Changed"


def test_copy_controls_use_the_existing_delegated_document_handler(
    client, development_world, login_as
):
    login_as(development_world.owner.email)

    response = client.get(
        f"/projects/{development_world.project.slug}/tickets/104",
        headers={"HX-Request": "true"},
    )
    javascript = Path("app/static/app.js").read_text()

    assert response.text.count('<button type="button"') >= 3
    assert response.text.count("data-copy-text=") == 2
    assert response.text.count('class="ticket-copy-status" role="status" aria-live="polite"') == 2
    copy_controls = re.findall(
        r'<button type="button"[^>]+data-copy-text=.*?</button>\s*'
        r'<span class="ticket-copy-status" role="status" aria-live="polite"></span>',
        response.text,
    )
    assert len(copy_controls) == 2
    assert 'data-copy-text="PAY-104"' in response.text
    assert 'data-copy-text="git checkout -b feature/pay-104-fix-timeout"' in response.text
    assert javascript.count('document.addEventListener("click"') == 1
    assert 'event.target.closest("[data-copy-text]")' in javascript
    assert 'status.textContent = "Copied"' in javascript
    assert 'status.textContent = "Copy failed"' in javascript
    assert 'querySelectorAll("[data-copy-text]")' not in javascript


def test_api_ticket_detail_exposes_active_development_for_members(client, development_world, login_as):
    login_as(development_world.member.email)

    body = client.get(f"/api/v1/tickets/{development_world.ticket_id}").json()

    assert body["branch_command"] == "git checkout -b feature/pay-104-fix-timeout"
    prs = [row for row in body["development"] if row["kind"] == "PULL_REQUEST"]
    safe = next(row for row in prs if row["number"] == 17)
    assert (safe["state"], safe["review_state"], safe["ci_state"]) == (
        "OPEN",
        "APPROVED",
        "PASSED",
    )
    assert safe["html_url"] == "https://github.com/acme/api/pull/17"
    assert next(row for row in prs if row["number"] == 16)["html_url"] is None
    assert all(row["repository_full_name"] != "acme/archived" for row in body["development"])
