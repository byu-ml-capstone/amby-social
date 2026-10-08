from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.models import (
    GitHubConnectState,
    GitHubInstallation,
    IntegrationDelivery,
    ProjectGitHubConnection,
    ProjectGitHubRepository,
    utcnow,
)
from app.services import delete_project


def test_project_repository_is_unique_per_project(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
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
    session.add(
        ProjectGitHubRepository(
            project_id=project.id,
            installation_id=installation.id,
            github_repository_id=501,
            full_name="acme/api",
            html_url="https://github.com/acme/api",
            default_branch="main",
        )
    )
    session.commit()
    session.add(
        ProjectGitHubRepository(
            project_id=project.id,
            installation_id=installation.id,
            github_repository_id=501,
            full_name="acme/api",
            html_url="https://github.com/acme/api",
            default_branch="main",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_competing_project_installation_bindings_allow_exactly_one(
    engine, session, make_user, make_project
):
    owner = make_user(email="binding-owner@example.com")
    project = make_project(owner, name="Binding Race")
    first = GitHubInstallation(
        github_installation_id=7001,
        account_id=1,
        account_login="one",
        connected_by_id=owner.id,
    )
    second = GitHubInstallation(
        github_installation_id=7002,
        account_id=2,
        account_login="two",
        connected_by_id=owner.id,
    )
    session.add_all([first, second])
    session.commit()
    first_id, second_id = first.id, second.id
    with Session(engine) as left, Session(engine) as right:
        assert left.get(ProjectGitHubConnection, project.id) is None
        assert right.get(ProjectGitHubConnection, project.id) is None
        left.add(
            ProjectGitHubConnection(project_id=project.id, installation_id=first_id)
        )
        right.add(
            ProjectGitHubConnection(project_id=project.id, installation_id=second_id)
        )
        left.commit()
        with pytest.raises(IntegrityError):
            right.commit()
    stored = session.get(ProjectGitHubConnection, project.id)
    assert stored.installation_id in {first_id, second_id}


def test_delivery_identity_is_provider_scoped(session):
    session.add(
        IntegrationDelivery(
            provider="GITHUB", delivery_id="delivery-1", event_type="push"
        )
    )
    session.commit()
    session.add(
        IntegrationDelivery(
            provider="GITHUB",
            delivery_id="delivery-1",
            event_type="pull_request",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_delete_project_removes_state_binding_and_repositories(
    session, make_user, make_project
):
    owner = make_user(email="delete-github@example.com")
    project = make_project(owner, name="Delete GitHub")
    installation = GitHubInstallation(
        github_installation_id=7003,
        account_id=3,
        account_login="delete",
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
    session.add(
        GitHubConnectState(
            id="a" * 64,
            project_id=project.id,
            user_id=owner.id,
            expires_at=utcnow() + timedelta(minutes=10),
        )
    )
    session.commit()
    delete_project(session, project, project.slug)
    assert session.get(ProjectGitHubConnection, project.id) is None
    assert session.get(GitHubConnectState, "a" * 64) is None
