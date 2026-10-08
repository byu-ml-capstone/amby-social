from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlmodel import select

from app.config import Settings
from app.github_sync import (
    aggregate_ci_state,
    aggregate_review_state,
    extract_ticket_numbers,
    insert_linked_commit,
    reconcile_pull_request_links,
    upsert_pull_request,
)
from app.models import (
    GitHubArtifact,
    GitHubArtifactState,
    GitHubCIState,
    GitHubInstallation,
    GitHubReviewState,
    ProjectGitHubConnection,
    ProjectGitHubRepository,
    Ticket,
    TicketGitLink,
    TicketStatus,
)
from app.services import create_ticket


@pytest.fixture
def github_sync_world(session, make_user, make_project):
    owner = make_user(email="sync-owner@example.com")
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
        ProjectGitHubConnection(
            project_id=project.id,
            installation_id=installation.id,
        )
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
        project=project,
        connection=connection,
        first=first,
        second=second,
    )


def test_extract_ticket_numbers_is_case_insensitive_and_boundary_aware():
    assert extract_ticket_numbers(
        "PAY", ["PAY-104 (pay-7) XPAY-2 PAY-0 PAY--1 PAY-9X"]
    ) == {7, 104}


def test_extract_ticket_numbers_ignores_oversized_reference_and_keeps_valid():
    assert extract_ticket_numbers("PAY", [f"PAY-{'9' * 5000} PAY-104"]) == {104}


def test_review_aggregation_uses_latest_effective_review_per_login():
    reviews = [
        {
            "user": {"login": "sam"},
            "state": "APPROVED",
            "submitted_at": "2026-09-17T10:00:00Z",
        },
        {
            "user": {"login": "sam"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-09-17T11:00:00Z",
        },
    ]
    assert aggregate_review_state(reviews) is GitHubReviewState.CHANGES_REQUESTED


def test_review_dismissal_removes_only_that_reviewers_effective_decision():
    reviews = [
        {
            "user": {"login": "sam"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-09-17T10:00:00Z",
        },
        {
            "user": {"login": "lee"},
            "state": "APPROVED",
            "submitted_at": "2026-09-17T10:30:00Z",
        },
        {
            "user": {"login": "sam"},
            "state": "DISMISSED",
            "submitted_at": "2026-09-17T11:00:00Z",
        },
    ]
    assert aggregate_review_state(reviews) is GitHubReviewState.APPROVED


@pytest.mark.parametrize("status", ["waiting", "requested", "pending"])
def test_ci_waiting_requested_and_pending_are_pending(status):
    assert aggregate_ci_state(
        [{"status": status, "conclusion": None}]
    ) is GitHubCIState.PENDING


def test_ci_stale_conclusion_is_failed_and_empty_is_none():
    assert aggregate_ci_state(
        [{"status": "completed", "conclusion": "stale"}]
    ) is GitHubCIState.FAILED
    assert aggregate_ci_state([]) is GitHubCIState.NONE


def test_ci_failure_outranks_pending():
    assert aggregate_ci_state(
        [
            {"status": "waiting", "conclusion": None},
            {"status": "completed", "conclusion": "failure"},
        ]
    ) is GitHubCIState.FAILED


@pytest.mark.parametrize(
    "check",
    [
        {},
        {"status": "completed", "conclusion": None},
        {"status": "unknown", "conclusion": "success"},
        {"status": "", "conclusion": ""},
    ],
)
def test_ci_malformed_or_unknown_check_is_none(check):
    assert aggregate_ci_state([check]) is GitHubCIState.NONE


@pytest.mark.parametrize("conclusion", ["success", "neutral", "skipped"])
def test_ci_completed_successful_conclusions_are_passed(conclusion):
    assert aggregate_ci_state(
        [{"status": "completed", "conclusion": conclusion}]
    ) is GitHubCIState.PASSED


def _pull(title="PAY-1 PAY-2", body="", branch="feature/pay-1"):
    return {
        "id": 9001,
        "node_id": "PR_node_9001",
        "number": 17,
        "title": title,
        "body": body,
        "draft": False,
        "state": "open",
        "merged_at": None,
        "html_url": "https://github.com/acme/api/pull/17",
        "user": {"login": "octocat"},
        "head": {"ref": branch, "sha": "a" * 40},
        "updated_at": "2026-09-17T12:00:00Z",
    }


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"draft": True, "merged_at": "2026-09-17T12:00:00Z"}, GitHubArtifactState.DRAFT),
        ({"draft": False, "merged_at": "2026-09-17T12:00:00Z"}, GitHubArtifactState.MERGED),
        ({"draft": False, "state": "closed"}, GitHubArtifactState.CLOSED),
        ({"draft": False, "state": "open"}, GitHubArtifactState.OPEN),
    ],
)
def test_pull_state_mapping_is_exact(session, github_sync_world, changes, expected):
    artifact = upsert_pull_request(
        session, github_sync_world.connection, {**_pull(), **changes}
    )
    assert artifact.state is expected


def test_pull_rejects_empty_external_identity_before_persistence(
    session, github_sync_world
):
    with pytest.raises(ValueError, match="invalid pull request payload"):
        upsert_pull_request(
            session,
            github_sync_world.connection,
            {**_pull(), "id": "", "node_id": ""},
        )
    assert session.exec(select(GitHubArtifact)).all() == []


@pytest.mark.parametrize(
    "html_url",
    [
        "http://github.com/acme/api/pull/17",
        "https://user@github.com/acme/api/pull/17",
        "https://example.com/acme/api/pull/17",
        "https://github.com:444/acme/api/pull/17",
    ],
)
def test_pull_rejects_urls_outside_configured_https_origin(
    session, github_sync_world, html_url
):
    with pytest.raises(ValueError, match="configured HTTPS web origin"):
        upsert_pull_request(
            session,
            github_sync_world.connection,
            {**_pull(), "html_url": html_url},
            Settings(github_web_url="https://github.com"),
        )
    assert session.exec(select(GitHubArtifact)).all() == []


def test_pull_upsert_persists_mapping_and_reconciles_links(
    session, github_sync_world
):
    world = github_sync_world
    artifact = upsert_pull_request(session, world.connection, _pull())
    reconcile_pull_request_links(
        session,
        world.connection,
        artifact,
        ["PAY-1 PAY-2", "", "feature/pay-1"],
    )
    session.commit()
    assert artifact.state is GitHubArtifactState.OPEN
    assert artifact.head_sha == "a" * 40
    assert artifact.occurred_at.replace(tzinfo=UTC) == datetime(
        2026, 9, 17, 12, tzinfo=UTC
    )
    linked = session.exec(
        select(Ticket.ticket_number)
        .join(TicketGitLink, TicketGitLink.ticket_id == Ticket.id)
        .where(TicketGitLink.artifact_id == artifact.id)
        .order_by(Ticket.ticket_number)
    ).all()
    assert linked == [1, 2]
    reconcile_pull_request_links(
        session,
        world.connection,
        artifact,
        ["PAY-2", None, None],
    )
    session.commit()
    linked = session.exec(
        select(Ticket.ticket_number)
        .join(TicketGitLink, TicketGitLink.ticket_id == Ticket.id)
        .where(TicketGitLink.artifact_id == artifact.id)
    ).all()
    assert linked == [2]


def test_pull_head_change_clears_ci_state_and_preserves_review_state(
    session, github_sync_world
):
    artifact = upsert_pull_request(session, github_sync_world.connection, _pull())
    artifact.ci_state = GitHubCIState.PASSED
    artifact.review_state = GitHubReviewState.APPROVED
    session.add(artifact)
    session.commit()
    pull = _pull()
    pull["head"]["sha"] = "b" * 40

    updated = upsert_pull_request(session, github_sync_world.connection, pull)

    assert updated.id == artifact.id
    assert updated.head_sha == "b" * 40
    assert updated.ci_state is GitHubCIState.NONE
    assert updated.review_state is GitHubReviewState.APPROVED


def test_pull_link_reconciliation_does_not_change_ticket_status(
    session, github_sync_world
):
    github_sync_world.first.status = TicketStatus.IN_PROGRESS
    session.add(github_sync_world.first)
    session.commit()
    artifact = upsert_pull_request(session, github_sync_world.connection, _pull())
    reconcile_pull_request_links(
        session, github_sync_world.connection, artifact, ["PAY-1"]
    )
    session.commit()
    session.refresh(github_sync_world.first)
    assert github_sync_world.first.status is TicketStatus.IN_PROGRESS


def test_pull_changes_remain_in_callers_transaction(session, github_sync_world):
    artifact = upsert_pull_request(session, github_sync_world.connection, _pull())
    reconcile_pull_request_links(
        session, github_sync_world.connection, artifact, ["PAY-1"]
    )
    session.rollback()
    assert session.exec(select(GitHubArtifact)).all() == []
    assert session.exec(select(TicketGitLink)).all() == []


def test_commit_uses_safe_html_url_and_keeps_first_links(
    session, github_sync_world
):
    world = github_sync_world
    sha = "b" * 40
    commit = {
        "id": sha,
        "message": "PAY-1 fix timeout\nbody",
        "timestamp": "2026-09-17T13:00:00Z",
        "author": {"username": "sam", "name": "Sam"},
        "committer": {"username": "bot", "name": "Bot"},
        "url": "javascript:alert(1)",
    }
    artifact = insert_linked_commit(session, world.connection, commit)
    session.commit()
    assert artifact.html_url == f"https://github.com/acme/api/commit/{sha}"
    assert artifact.title == "PAY-1 fix timeout"
    assert artifact.author_login == "sam"
    replay = insert_linked_commit(
        session,
        world.connection,
        {**commit, "message": "PAY-2 changed"},
    )
    session.commit()
    assert replay.id == artifact.id
    links = session.exec(
        select(TicketGitLink).where(TicketGitLink.artifact_id == artifact.id)
    ).all()
    assert [link.ticket_id for link in links] == [world.first.id]
    assert (
        insert_linked_commit(
            session,
            world.connection,
            {**commit, "id": "c" * 40, "message": "no key"},
        )
        is None
    )


@pytest.mark.parametrize("sha", ["a" * 39, "g" * 40, None])
def test_commit_rejects_non_sha_ids_before_persistence(
    session, github_sync_world, sha
):
    assert (
        insert_linked_commit(
            session,
            github_sync_world.connection,
            {
                "id": sha,
                "message": "PAY-1 malformed",
                "timestamp": "2026-09-17T13:00:00Z",
            },
        )
        is None
    )
    assert session.exec(select(GitHubArtifact)).all() == []
