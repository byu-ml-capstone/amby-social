import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import update
from sqlmodel import select

from app.config import settings
from app.github import consume_github_state, issue_github_state, read_github_state
from app.models import GitHubConnectState, ProjectMember, Role
from app.services import delete_project

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def _assert_http_error(status_code, detail, call):
    with pytest.raises(HTTPException) as error:
        call()
    assert (error.value.status_code, error.value.detail) == (status_code, detail)


def test_state_is_signed_stored_as_hash_and_read_does_not_consume(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)

    signed = issue_github_state(
        session,
        project.id,
        owner.id,
        pending_installation_id=7001,
        now=NOW,
    )

    payload = URLSafeTimedSerializer(settings.session_secret, salt="kf-github-connect").loads(
        signed
    )
    row = session.exec(select(GitHubConnectState)).one()
    assert payload.keys() == {"nonce"}
    assert row.id == hashlib.sha256(payload["nonce"].encode()).hexdigest()
    assert len(row.id) == 64
    assert signed not in row.id
    assert row.pending_installation_id == 7001
    assert read_github_state(session, signed, owner.id, now=NOW).consumed_at is None
    assert session.get(GitHubConnectState, row.id).consumed_at is None


def test_state_is_consumed_once_atomically(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    signed = issue_github_state(session, project.id, owner.id, now=NOW)

    consumed = consume_github_state(session, signed, owner.id, now=NOW)

    assert consumed.project_id == project.id
    assert consumed.consumed_at == NOW
    _assert_http_error(
        400,
        "GitHub state already used",
        lambda: read_github_state(session, signed, owner.id, now=NOW),
    )
    _assert_http_error(
        400,
        "GitHub state already used",
        lambda: consume_github_state(session, signed, owner.id, now=NOW),
    )


def test_state_rejects_tampering_unknown_nonce_and_wrong_user(session, make_user, make_project):
    owner = make_user()
    stranger = make_user(email="stranger@example.com")
    project = make_project(owner)
    signed = issue_github_state(session, project.id, owner.id, now=NOW)
    serializer = URLSafeTimedSerializer(settings.session_secret, salt="kf-github-connect")
    unknown = serializer.dumps({"nonce": "not-the-issued-nonce"})

    for state, detail in (
        (f"{signed}tampered", "Invalid GitHub state"),
        (unknown, "Invalid GitHub state"),
    ):
        _assert_http_error(
            400,
            detail,
            lambda state=state: read_github_state(session, state, owner.id, now=NOW),
        )
    _assert_http_error(
        403,
        "GitHub state belongs to another user",
        lambda: read_github_state(session, signed, stranger.id, now=NOW),
    )


def test_state_expires_after_ten_minutes(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    signed = issue_github_state(session, project.id, owner.id, now=NOW)

    _assert_http_error(
        400,
        "GitHub state expired",
        lambda: read_github_state(session, signed, owner.id, now=NOW + timedelta(seconds=601)),
    )


def test_state_rejects_owner_who_lost_ownership(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    signed = issue_github_state(session, project.id, owner.id, now=NOW)
    session.exec(
        update(ProjectMember)
        .where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == owner.id,
        )
        .values(role=Role.MEMBER)
    )
    session.commit()

    _assert_http_error(
        403,
        "Project owner role required",
        lambda: read_github_state(session, signed, owner.id, now=NOW),
    )


def test_state_is_invalid_after_project_deletion(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    signed = issue_github_state(session, project.id, owner.id, now=NOW)

    delete_project(session, project, project.slug)

    _assert_http_error(
        400,
        "Invalid GitHub state",
        lambda: read_github_state(session, signed, owner.id, now=NOW),
    )
