import os
import subprocess
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

from app import models  # noqa: F401  — imported for its side effect of registering tables
from app.db import make_engine
from app.models import (
    GitHubArtifact,
    GitHubArtifactKind,
    GitHubCIState,
    GitHubInstallation,
    IntegrationDelivery,
    ProjectGitHubConnection,
    ProjectGitHubRepository,
    Sprint,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    TicketGitLink,
    TicketStatus,
    User,
)


def test_key_migration_backfills_existing_projects(tmp_path):
    db = tmp_path / "legacy.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
    subprocess.run(["uv", "run", "alembic", "upgrade", "c41d8e7f2a10"], check=True, env=env)
    with make_engine(f"sqlite:///{db}").begin() as connection:
        connection.execute(
            text(
                "INSERT INTO project "
                "(id,name,slug,webhook_type,next_ticket_number,created_at) VALUES "
                "('a','Payment Gateway','payment-gateway','NONE',1,'2026-01-01'),"
                "('b','Payments Admin','payments-admin','NONE',1,'2026-01-02')"
            )
        )

    subprocess.run(["uv", "run", "alembic", "upgrade", "d93f7a21c4e8"], check=True, env=env)
    with make_engine(f"sqlite:///{db}").connect() as connection:
        assert connection.execute(text("SELECT key FROM project ORDER BY id")).scalars().all() == [
            "PAY",
            "PAY2",
        ]

    subprocess.run(["uv", "run", "alembic", "downgrade", "c41d8e7f2a10"], check=True, env=env)
    columns = {
        column["name"] for column in inspect(make_engine(f"sqlite:///{db}")).get_columns("project")
    }
    assert "key" not in columns


def test_chat_migration_round_trip(tmp_path):
    db = tmp_path / "legacy-chat.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
    subprocess.run(["uv", "run", "alembic", "upgrade", "d93f7a21c4e8"], check=True, env=env)
    with make_engine(f"sqlite:///{db}").begin() as connection:
        connection.execute(
            text(
                "INSERT INTO project "
                "(id,name,slug,key,webhook_type,webhook_url,next_ticket_number,created_at) "
                "VALUES ('p','Project','project','PRJ','SLACK',"
                "'https://hooks.example.test/legacy',1,'2026-01-01')"
            )
        )
    subprocess.run(["uv", "run", "alembic", "upgrade", "e4b9c52d8fa1"], check=True, env=env)
    with make_engine(f"sqlite:///{db}").connect() as connection:
        assert connection.execute(text("SELECT provider,url FROM project_chat_webhook")).one() == (
            "SLACK",
            "https://hooks.example.test/legacy",
        )
    subprocess.run(["uv", "run", "alembic", "downgrade", "d93f7a21c4e8"], check=True, env=env)
    with make_engine(f"sqlite:///{db}").connect() as connection:
        assert connection.execute(
            text("SELECT webhook_type,webhook_url FROM project WHERE id='p'")
        ).one() == ("SLACK", "https://hooks.example.test/legacy")


def test_ticket_due_date_migration_round_trip(tmp_path):
    db = tmp_path / "ticket-due-date.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
    subprocess.run(["uv", "run", "alembic", "upgrade", "5d0b32a81e77"], check=True, env=env)

    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)
    inspector = inspect(make_engine(f"sqlite:///{db}"))
    due_date = next(
        column for column in inspector.get_columns("ticket") if column["name"] == "due_date"
    )
    assert due_date["nullable"]
    assert "ix_ticket_due_date" in {index["name"] for index in inspector.get_indexes("ticket")}

    subprocess.run(["uv", "run", "alembic", "downgrade", "5d0b32a81e77"], check=True, env=env)
    inspector = inspect(make_engine(f"sqlite:///{db}"))
    assert "due_date" not in {column["name"] for column in inspector.get_columns("ticket")}
    assert "ix_ticket_due_date" not in {index["name"] for index in inspector.get_indexes("ticket")}


def test_migration_produces_the_same_tables_as_the_models(tmp_path):
    db = tmp_path / "migrated.db"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        env={"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]},
    )
    inspector = inspect(make_engine(f"sqlite:///{db}"))
    migrated = set(inspector.get_table_names())
    expected = set(SQLModel.metadata.tables) | {"alembic_version"}
    assert expected <= migrated

    # Table names agreeing isn't enough — a dropped or renamed column would still
    # pass that check. Compare each table's actual columns against the model.
    for table_name, table in SQLModel.metadata.tables.items():
        migrated_columns = {col["name"] for col in inspector.get_columns(table_name)}
        model_columns = {col.name for col in table.columns}
        assert migrated_columns == model_columns, f"{table_name} columns disagree"


def test_api_token_migration_creates_indexes_and_user_foreign_key(tmp_path):
    db = tmp_path / "migrated.db"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        env={"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]},
    )
    inspector = inspect(make_engine(f"sqlite:///{db}"))

    expected_columns = {
        "id",
        "user_id",
        "label",
        "prefix",
        "token_hash",
        "scope",
        "created_at",
        "expires_at",
        "last_used_at",
        "revoked_at",
    }
    assert expected_columns == {column["name"] for column in inspector.get_columns("api_token")}
    indexes = {index["name"]: index for index in inspector.get_indexes("api_token")}
    assert not indexes["ix_api_token_user_id"]["unique"]
    assert indexes["ix_api_token_token_hash"]["unique"]
    referred_tables = {
        foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("api_token")
    }
    assert referred_tables == {"user"}


def test_api_token_migration_downgrade_and_upgrade_are_safe(tmp_path):
    db = tmp_path / "migrated.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)
    subprocess.run(["uv", "run", "alembic", "downgrade", "6c25b12d1a91"], check=True, env=env)

    downgraded_tables = inspect(make_engine(f"sqlite:///{db}")).get_table_names()
    assert "api_token" not in downgraded_tables
    assert "ticket_comment" not in downgraded_tables

    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)
    assert "api_token" in inspect(make_engine(f"sqlite:///{db}")).get_table_names()


def test_sprint_migration_creates_constraints(tmp_path):
    db = tmp_path / "migrated.db"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        env={"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]},
    )
    inspector = inspect(make_engine(f"sqlite:///{db}"))

    assert {"sprint", "sprint_ticket_history"} <= set(inspector.get_table_names())
    assert {"sprint_id", "first_sprint_entered_at", "delayed_days", "rollover_count"} <= {
        column["name"] for column in inspector.get_columns("ticket")
    }
    assert {"uq_sprint_active_project", "uq_sprint_planning_project"} <= {
        index["name"] for index in inspector.get_indexes("sprint")
    }
    assert {
        foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("ticket")
    } >= {"sprint"}


def test_sprint_migration_rejects_invalid_dates(tmp_path):
    db = tmp_path / "migrated.db"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        env={"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]},
    )
    engine = make_engine(f"sqlite:///{db}")

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO project (id, name, slug, key, next_ticket_number, created_at) "
                "VALUES ('project-1', 'Project', 'project', 'PRO', 1, CURRENT_TIMESTAMP)"
            )
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO sprint (id, project_id, name, goal, status, start_date, end_date) "
                    "VALUES ('sprint-1', 'project-1', 'Sprint 1', 'Impossible', 'PLANNING', "
                    "'2026-09-28', '2026-09-21')"
                )
            )


def test_sprint_migration_enforces_status_and_history_constraints(tmp_path):
    db = tmp_path / "migrated.db"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        env={"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]},
    )
    engine = make_engine(f"sqlite:///{db}")

    with Session(engine) as session:
        owner = User(name="Owner", email="owner@example.com", password_hash="x")
        project = models.Project(name="Project", slug="project", key="PRO")
        session.add_all([owner, project])
        session.commit()
        ticket = Ticket(ticket_number=1, project_id=project.id, title="Ticket", creator_id=owner.id)
        session.add(ticket)
        session.commit()

        sprint_kwargs = {
            "project_id": project.id,
            "goal": "Goal",
            "start_date": date(2026, 9, 21),
            "end_date": date(2026, 9, 28),
        }
        planning = Sprint(name="Planning", status=SprintStatus.PLANNING, **sprint_kwargs)
        session.add(planning)
        session.commit()
        session.add(Sprint(name="Planning 2", status=SprintStatus.PLANNING, **sprint_kwargs))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        active = Sprint(name="Active", status=SprintStatus.ACTIVE, **sprint_kwargs)
        session.add(active)
        session.commit()
        session.add(Sprint(name="Active 2", status=SprintStatus.ACTIVE, **sprint_kwargs))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add_all(
            [
                Sprint(name="Closed 1", status=SprintStatus.CLOSED, **sprint_kwargs),
                Sprint(name="Closed 2", status=SprintStatus.CLOSED, **sprint_kwargs),
            ]
        )
        session.commit()

        session.add(
            SprintTicketHistory(
                sprint_id=planning.id,
                ticket_id=ticket.id,
                status_at_close=TicketStatus.DONE,
                was_completed=True,
            )
        )
        session.commit()
        session.add(
            SprintTicketHistory(
                sprint_id=planning.id,
                ticket_id=ticket.id,
                status_at_close=TicketStatus.BACKLOG,
                was_completed=False,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_github_migration_creates_tables_constraints_and_indexes(tmp_path):
    db = tmp_path / "github-migrated.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)

    inspector = inspect(make_engine(f"sqlite:///{db}"))
    github_tables = {
        "github_installation",
        "project_github_connection",
        "github_connect_state",
        "project_github_repository",
        "github_artifact",
        "ticket_git_link",
        "integration_delivery",
    }
    assert github_tables <= set(inspector.get_table_names())

    expected_unique_columns = {
        "github_installation": {("github_installation_id",)},
        "project_github_connection": {("project_id", "installation_id")},
        "project_github_repository": {("project_id", "github_repository_id")},
        "github_artifact": {("repository_connection_id", "kind", "external_id")},
        "integration_delivery": {("provider", "delivery_id")},
    }
    for table_name, expected in expected_unique_columns.items():
        actual = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table_name)
        }
        assert expected <= actual

    assert inspector.get_pk_constraint("ticket_git_link")["constrained_columns"] == [
        "ticket_id",
        "artifact_id",
    ]
    assert any(
        foreign_key["constrained_columns"] == ["project_id", "installation_id"]
        and foreign_key["referred_table"] == "project_github_connection"
        and foreign_key["referred_columns"] == ["project_id", "installation_id"]
        for foreign_key in inspector.get_foreign_keys("project_github_repository")
    )

    expected_indexes = {
        "project_github_connection": {"ix_project_github_connection_installation_id"},
        "github_connect_state": {
            "ix_github_connect_state_project_id",
            "ix_github_connect_state_user_id",
        },
        "project_github_repository": {
            "ix_project_github_repository_project_id",
            "ix_project_github_repository_installation_id",
            "ix_project_github_repository_github_repository_id",
        },
        "github_artifact": {"ix_github_artifact_repository_connection_id"},
    }
    for table_name, expected in expected_indexes.items():
        assert expected <= {index["name"] for index in inspector.get_indexes(table_name)}


def test_github_migration_downgrade_and_upgrade_are_safe(tmp_path):
    db = tmp_path / "github-round-trip.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)
    engine = make_engine(f"sqlite:///{db}")
    assert "github_installation" in inspect(engine).get_table_names()

    subprocess.run(
        ["uv", "run", "alembic", "downgrade", "e4b9c52d8fa1"],
        check=True,
        env=env,
    )
    assert "github_installation" not in inspect(engine).get_table_names()

    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)
    assert "github_installation" in inspect(engine).get_table_names()


def test_github_migration_rejects_duplicate_rows(tmp_path):
    db = tmp_path / "github-constraints.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)
    engine = make_engine(f"sqlite:///{db}")

    with Session(engine) as session:
        owner = User(
            name="Owner",
            email="github-owner@example.com",
            password_hash="hash",
        )
        project = models.Project(name="GitHub", slug="github", key="GIT")
        ticket = Ticket(
            ticket_number=1,
            project_id=project.id,
            title="GitHub ticket",
            creator_id=owner.id,
        )
        installation = GitHubInstallation(
            github_installation_id=7001,
            account_id=91,
            account_login="acme",
            connected_by_id=owner.id,
        )
        binding = ProjectGitHubConnection(
            project_id=project.id,
            installation_id=installation.id,
        )
        repository = ProjectGitHubRepository(
            project_id=project.id,
            installation_id=installation.id,
            github_repository_id=501,
            full_name="acme/api",
            html_url="https://github.com/acme/api",
            default_branch="main",
        )
        artifact = GitHubArtifact(
            repository_connection_id=repository.id,
            kind=GitHubArtifactKind.PULL_REQUEST,
            external_id="PR_kwDO1",
            number=12,
            title="GIT-1",
            html_url="https://github.com/acme/api/pull/12",
            author_login="octocat",
            ci_state=GitHubCIState.NONE,
            occurred_at=datetime(2026, 9, 17, tzinfo=UTC),
        )
        session.add_all([owner, project])
        session.commit()
        session.add_all([ticket, installation])
        session.commit()
        session.add(binding)
        session.commit()
        session.add(repository)
        session.commit()
        session.add(artifact)
        session.commit()
        session.add_all(
            [
                TicketGitLink(ticket_id=ticket.id, artifact_id=artifact.id),
                IntegrationDelivery(
                    provider="GITHUB",
                    delivery_id="delivery-1",
                    event_type="pull_request",
                ),
            ]
        )
        session.commit()
        owner_id = owner.id
        project_id = project.id
        installation_id = installation.id
        repository_id = repository.id
        artifact_id = artifact.id
        ticket_id = ticket.id
        session.expunge_all()

        session.add(
            GitHubInstallation(
                github_installation_id=7001,
                account_id=92,
                account_login="duplicate",
                connected_by_id=owner_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        second_installation = GitHubInstallation(
            github_installation_id=7002,
            account_id=92,
            account_login="other",
            connected_by_id=owner_id,
        )
        session.add(second_installation)
        session.commit()
        session.add(
            ProjectGitHubConnection(
                project_id=project_id,
                installation_id=second_installation.id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            ProjectGitHubRepository(
                project_id=project_id,
                installation_id=installation_id,
                github_repository_id=501,
                full_name="acme/api-copy",
                html_url="https://github.com/acme/api-copy",
                default_branch="main",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            GitHubArtifact(
                repository_connection_id=repository_id,
                kind=GitHubArtifactKind.PULL_REQUEST,
                external_id="PR_kwDO1",
                title="Duplicate",
                html_url="https://github.com/acme/api/pull/12",
                author_login="octocat",
                ci_state=GitHubCIState.NONE,
                occurred_at=datetime(2026, 9, 17, tzinfo=UTC),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(TicketGitLink(ticket_id=ticket_id, artifact_id=artifact_id))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            IntegrationDelivery(
                provider="GITHUB",
                delivery_id="delivery-1",
                event_type="push",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_gmail_migration_round_trip(tmp_path):
    db = tmp_path / "gmail.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)
    assert "gmail_connection" in inspect(make_engine(f"sqlite:///{db}")).get_table_names()
    subprocess.run(["uv", "run", "alembic", "downgrade", "f2a1b3c4d5e6"], check=True, env=env)
    assert "gmail_connection" not in inspect(make_engine(f"sqlite:///{db}")).get_table_names()


def test_attachment_migration_round_trip(tmp_path):
    db = tmp_path / "attachments.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)
    inspector = inspect(make_engine(f"sqlite:///{db}"))
    assert "ticket_attachment" in inspector.get_table_names()
    assert {index["name"] for index in inspector.get_indexes("ticket_attachment")} == {
        "ix_ticket_attachment_ticket_id",
        "ix_ticket_attachment_project_id",
    }
    subprocess.run(["uv", "run", "alembic", "downgrade", "a3f7c9e2b815"], check=True, env=env)
    assert "ticket_attachment" not in inspect(make_engine(f"sqlite:///{db}")).get_table_names()
