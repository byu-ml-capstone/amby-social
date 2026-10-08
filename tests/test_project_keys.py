from sqlmodel import Session

from app.models import Project
from app.schemas import ProjectUpdate
from app.services import create_project, update_project


def test_project_key_is_stable_and_collision_safe(session, make_user):
    owner = make_user(email="key-owner@example.com")
    first = create_project(session, "Payment Gateway", owner)
    second = create_project(session, "Payments Admin", owner)

    update_project(session, first, name="Checkout")

    assert (first.key, second.key) == ("PAY", "PAY2")


def test_project_keys_are_short_uppercase_ascii_alphanumeric(session, make_user):
    owner = make_user(email="key-format@example.com")
    project = create_project(session, "!!! Gateway", owner)

    assert project.key == "PRJ"
    assert project.key.isascii() and project.key.isalnum() and project.key.isupper()
    assert len(project.key) <= 10
    assert Project.__table__.c.key.type.length == 10


def test_project_key_is_exposed_but_not_updateable(client, make_user, login_as):
    make_user(email="key-api@example.com")
    login_as("key-api@example.com")

    created = client.post("/api/v1/projects", json={"name": "Payment Gateway"})
    updated = client.patch(
        "/api/v1/projects/payment-gateway",
        json={"name": "Checkout", "key": "NOPE"},
    )

    assert created.status_code == 201
    assert created.json()["key"] == "PAY"
    assert updated.status_code == 200
    assert updated.json()["key"] == "PAY"
    assert "key" not in ProjectUpdate.model_fields


def test_project_settings_displays_key_as_read_only(client, make_user, make_project, login_as):
    owner = make_user(email="key-settings@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/settings")

    assert page.status_code == 200
    assert f"<code>{project.key}</code>" in page.text
    assert 'name="key"' not in page.text


def test_project_creation_retries_a_concurrent_key_collision(
    session, engine, make_user, monkeypatch
):
    owner = make_user(email="key-race@example.com")
    from app import services

    original_allocate = services.allocate_project_key
    state = {"raced": False}

    def allocate_then_race(current_session, name):
        key = original_allocate(current_session, name)
        if not state["raced"]:
            state["raced"] = True
            with Session(engine) as racer_session:
                racer_session.add(Project(name="Racer", slug="racer", key=key))
                racer_session.commit()
        return key

    monkeypatch.setattr(services, "allocate_project_key", allocate_then_race)

    project = create_project(session, "Payment Gateway", owner)

    assert project.key == "PAY2"
