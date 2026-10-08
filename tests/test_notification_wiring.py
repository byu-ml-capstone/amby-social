import json
import time

import httpx
import pytest
from sqlmodel import Session, select

import app.notifications as notifications
from app.models import Project, ProjectChatWebhook, Ticket, WebhookType
from app.notifications import EVENT_TICKET_CREATED, dispatch, schedule
from app.services import create_ticket, set_chat_webhook


@pytest.fixture
def slack_project(engine, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as session:
        stored = session.get(Project, project.id)
        set_chat_webhook(session, stored, WebhookType.SLACK, "https://example.com/hook")
    return project


def test_creating_a_ticket_dispatches_one_payload(client, slack_project, login_as, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.notifications.dispatch", lambda wt, url, payload, client=None: sent.append(payload)
    )
    login_as("ada@example.com")
    client.post("/api/v1/tickets", json={"slug": slack_project.slug, "title": "T"})
    assert len(sent) == 1
    assert sent[0]["event"] == "TICKET_CREATED"
    assert sent[0]["ticket_number"] == 1


def test_only_done_transitions_notify(client, slack_project, login_as, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.notifications.dispatch", lambda wt, url, payload, client=None: sent.append(payload)
    )
    login_as("ada@example.com")
    ticket_id = client.post(
        "/api/v1/tickets", json={"slug": slack_project.slug, "title": "T"}
    ).json()["id"]
    sent.clear()
    client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "IN_PROGRESS"})
    assert sent == []
    client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "DONE"})
    assert [p["event"] for p in sent] == ["TICKET_DONE"]


def test_round_trip_to_done_sends_two_cards(client, slack_project, login_as, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.notifications.dispatch", lambda wt, url, payload, client=None: sent.append(payload)
    )
    login_as("ada@example.com")
    ticket_id = client.post(
        "/api/v1/tickets", json={"slug": slack_project.slug, "title": "T"}
    ).json()["id"]
    sent.clear()
    for target in ["DONE", "IN_PROGRESS", "DONE"]:
        client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": target})
    assert len(sent) == 2, "no duplicate suppression is intentional (D-03)"


def test_dispatch_runs_after_the_row_is_committed(
    client, slack_project, login_as, monkeypatch, engine
):
    visible: list[bool] = []

    def spy(webhook_type, url, payload, client=None):
        with Session(engine) as session:
            rows = session.exec(
                select(Ticket).where(Ticket.ticket_number == payload["ticket_number"])
            ).all()
            visible.append(len(rows) == 1)

    monkeypatch.setattr("app.notifications.dispatch", spy)
    login_as("ada@example.com")
    client.post("/api/v1/tickets", json={"slug": slack_project.slug, "title": "T"})
    assert visible == [True]


def test_black_hole_webhook_does_not_break_creation(
    client, slack_project, login_as, monkeypatch, caplog, engine
):
    monkeypatch.setattr("app.notifications.time.sleep", lambda seconds: None)
    with Session(engine) as session:
        webhook = session.exec(
            select(ProjectChatWebhook).where(ProjectChatWebhook.project_id == slack_project.id)
        ).one()
        webhook.url = "https://127.0.0.1:9/hook"
        session.add(webhook)
        session.commit()
    login_as("ada@example.com")
    with caplog.at_level("WARNING", logger="app.notifications"):
        response = client.post(
            "/api/v1/tickets", json={"slug": slack_project.slug, "title": "Survives"}
        )
    assert response.status_code == 201
    assert response.json()["ticket_number"] == 1
    assert "ticket_number=1" in caplog.text


class _RecordingTasks:
    """Stand-in for BackgroundTasks that records what was enqueued, without
    running it, so we can inspect the argument after the session closes."""

    def __init__(self):
        self.calls: list[tuple] = []

    def add_task(self, func, *args, **kwargs):
        self.calls.append((func, args, kwargs))


def _two_destination_tasks(session, make_user, make_project, email):
    owner = make_user(email=email)
    project = session.get(Project, make_project(owner).id)
    first_url = "https://hooks.example.test/first"
    second_url = "https://hooks.example.test/second"
    set_chat_webhook(session, project, WebhookType.SLACK, first_url)
    set_chat_webhook(session, project, WebhookType.TEAMS, second_url)
    ticket = create_ticket(session, project, owner, title="Fan out")
    tasks = _RecordingTasks()
    schedule(tasks, session, project, EVENT_TICKET_CREATED, ticket)
    return tasks, first_url, second_url


def test_schedule_enqueues_only_a_plain_dict_that_outlives_the_session(
    engine, make_user, make_project
):
    owner = make_user(email="grace@example.com")
    project = make_project(owner)
    tasks = _RecordingTasks()
    with Session(engine) as session:
        stored = session.get(Project, project.id)
        set_chat_webhook(session, stored, WebhookType.SLACK, "https://example.com/hook")
        ticket = create_ticket(session, stored, owner, title="T")
        schedule(tasks, session, stored, EVENT_TICKET_CREATED, ticket)
    # The session above is now closed. Nothing ORM-shaped may have been
    # captured by the enqueued call -- touching an unloaded attribute on
    # `stored` or `ticket` here would raise DetachedInstanceError, so the
    # only way this passes is if `dispatch` was queued with a plain dict
    # built while the session was still open.
    assert len(tasks.calls) == 1
    func, args, kwargs = tasks.calls[0]
    assert func is dispatch
    webhook_type, webhook_url, payload = args
    assert type(payload) is dict  # exact type: a Ticket/Project must never masquerade here
    assert payload["event"] == "TICKET_CREATED"
    assert payload["ticket_number"] == 1
    assert payload["project_name"] == "Payment Gateway"
    assert json.dumps(payload)  # still plain and JSON-serializable after close


def test_no_webhook_configured_schedules_nothing(session, make_user, make_project):
    owner = make_user(email="frank@example.com")
    project = make_project(owner)
    ticket = create_ticket(session, project, owner, title="T")
    tasks = _RecordingTasks()
    schedule(tasks, session, project, EVENT_TICKET_CREATED, ticket)
    assert tasks.calls == []


def test_failed_first_dispatch_does_not_prevent_second(
    session, make_user, make_project, monkeypatch
):
    owner = make_user(email="fanout-owner@example.com")
    project = session.get(Project, make_project(owner).id)
    first_url = "https://hooks.example.test/first"
    second_url = "https://hooks.example.test/second"
    set_chat_webhook(session, project, WebhookType.SLACK, first_url)
    set_chat_webhook(session, project, WebhookType.TEAMS, second_url)
    rows = session.exec(
        select(ProjectChatWebhook)
        .where(ProjectChatWebhook.project_id == project.id)
        .order_by(ProjectChatWebhook.provider)
    ).all()
    assert [(row.provider, row.url) for row in rows] == [
        (WebhookType.SLACK, first_url),
        (WebhookType.TEAMS, second_url),
    ]

    ticket = create_ticket(session, project, owner, title="Fan out")
    tasks = _RecordingTasks()
    schedule(tasks, session, project, EVENT_TICKET_CREATED, ticket)
    assert len(tasks.calls) == 2
    assert [args[1] for _, args, _ in tasks.calls] == [first_url, second_url]

    attempted = []

    def post(_client, url, **kwargs):
        attempted.append(url)
        if url == first_url:
            raise httpx.ConnectError("down")
        return httpx.Response(200)

    monkeypatch.setattr(httpx.Client, "post", post)
    monkeypatch.setattr(notifications, "BACKOFF_SECONDS", ())
    for func, args, kwargs in tasks.calls:
        func(*args, **kwargs)

    assert attempted == [first_url, second_url]


def test_client_constructor_failure_does_not_prevent_second_destination(
    session, make_user, make_project, monkeypatch
):
    tasks, _, second_url = _two_destination_tasks(
        session, make_user, make_project, "constructor-owner@example.com"
    )
    delivered = []
    constructions = 0

    class WorkingClient:
        def post(self, url, **kwargs):
            delivered.append(url)
            return httpx.Response(200)

        def close(self):
            pass

    def client_factory(**kwargs):
        nonlocal constructions
        constructions += 1
        if constructions == 1:
            raise RuntimeError("constructor failed")
        return WorkingClient()

    monkeypatch.setattr(httpx, "Client", client_factory)
    for func, args, kwargs in tasks.calls:
        func(*args, **kwargs)

    assert delivered == [second_url]


def test_client_close_failure_does_not_prevent_second_destination(
    session, make_user, make_project, monkeypatch
):
    tasks, first_url, second_url = _two_destination_tasks(
        session, make_user, make_project, "close-owner@example.com"
    )
    delivered = []
    constructions = 0

    class LifecycleClient:
        def __init__(self, close_raises):
            self.close_raises = close_raises

        def post(self, url, **kwargs):
            delivered.append(url)
            return httpx.Response(200)

        def close(self):
            if self.close_raises:
                raise RuntimeError("close failed")

    def client_factory(**kwargs):
        nonlocal constructions
        constructions += 1
        return LifecycleClient(close_raises=constructions == 1)

    monkeypatch.setattr(httpx, "Client", client_factory)
    for func, args, kwargs in tasks.calls:
        func(*args, **kwargs)

    assert delivered == [first_url, second_url]


def test_creation_latency_excludes_delivery(client, slack_project, login_as, monkeypatch):
    monkeypatch.setattr("app.notifications.dispatch", lambda *a, **k: None)
    login_as("ada@example.com")
    started = time.perf_counter()
    response = client.post("/api/v1/tickets", json={"slug": slack_project.slug, "title": "T"})
    elapsed = time.perf_counter() - started
    assert response.status_code == 201
    assert elapsed < 0.5, f"handler took {elapsed * 1000:.0f} ms, budget is 500 ms"
