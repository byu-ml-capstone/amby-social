import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import GmailConnection, GmailConnectionStatus, Project
from app.services import delete_project


def _connection(project, owner):
    return GmailConnection(
        project_id=project.id,
        user_id=owner.id,
        google_email="marketing@example.com",
        refresh_token_enc="encrypted",
        history_id="100",
    )


def test_connection_defaults(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    row = _connection(project, owner)
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.status == GmailConnectionStatus.ACTIVE
    assert row.label_mapping == {}
    assert row.last_synced_at is None
    assert row.last_error is None


def test_one_gmail_connection_per_project(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    session.add(_connection(project, owner))
    session.commit()
    session.add(_connection(project, owner))
    with pytest.raises(IntegrityError):
        session.commit()


def test_project_delete_removes_gmail_connection(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    session.add(_connection(project, owner))
    session.commit()
    delete_project(session, session.get(Project, project.id), project.slug)
    session.commit()
    assert session.exec(select(GmailConnection)).all() == []
