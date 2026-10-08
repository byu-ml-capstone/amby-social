import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlmodel import select

import app.main as main
import app.routers.gmail as gmail_routes
from app.auth import make_csrf_token
from app.config import settings
from app.gmail import AUTH_URL, decrypt_token, encrypt_token, issue_gmail_state, read_gmail_state
from app.gmail import GmailClient as RealGmailClient
from app.models import GmailConnection, GmailConnectionStatus


@pytest.fixture
def configured_gmail(monkeypatch):
    for name, value in {
        "google_client_id": "gid",
        "google_client_secret": "gsecret",
        "google_redirect_uri": "https://kanban.example.com/integrations/gmail/callback",
        "token_encryption_key": Fernet.generate_key().decode(),
    }.items():
        monkeypatch.setattr(settings, name, value)
    # The lifespan starts the real poller once Gmail is configured; keep it idle in route tests.
    monkeypatch.setattr(main, "poll_forever", lambda: asyncio.sleep(3600))


@pytest.fixture
def world(make_user, make_project, add_member):
    owner = make_user(email="ada@example.com", name="Ada")
    member = make_user(email="bob@example.com", name="Bob")
    project = make_project(owner, name="Marketing")
    add_member(project, member)
    return SimpleNamespace(owner=owner, member=member, project=project)


def _csrf(user):
    return {"_csrf": make_csrf_token(user.id)}


def _use_google(
    monkeypatch,
    *,
    email="marketing@example.com",
    history_id="500",
    token_error=None,
    revoke_status=200,
    requests=None,
):
    labels = [
        {"id": "Label_1", "name": "Flyers", "type": "user"},
        {"id": "Label_2", "name": "Urgent", "type": "user"},
        {"id": "INBOX", "name": "INBOX", "type": "system"},
    ]

    def handler(request):
        if requests is not None:
            requests.append(request)
        if request.url.path == "/token":
            if token_error:
                return httpx.Response(400, json={"error": token_error})
            form = parse_qs(request.content.decode())
            body = {"access_token": "ya29.access", "expires_in": 3599}
            if form["grant_type"] == ["authorization_code"]:
                body["refresh_token"] = "1//refresh"
            return httpx.Response(200, json=body)
        if request.url.path == "/revoke":
            return httpx.Response(revoke_status)
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": email, "historyId": history_id})
        if request.url.path.endswith("/labels"):
            return httpx.Response(200, json={"labels": labels})
        raise AssertionError(f"unexpected Google request {request.url}")

    transport = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gmail_routes, "GmailClient", lambda: RealGmailClient(settings, transport))


def _connection(session, world, **overrides):
    values = {
        "project_id": world.project.id,
        "user_id": world.owner.id,
        "google_email": "marketing@example.com",
        "refresh_token_enc": encrypt_token("1//refresh"),
        "history_id": "100",
        "label_mapping": {"Label_1": {"name": "Flyers", "type": "TASK", "priority": "MEDIUM"}},
    }
    values.update(overrides)
    row = GmailConnection(**values)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _stored(session, world):
    session.expire_all()
    return session.exec(
        select(GmailConnection).where(GmailConnection.project_id == world.project.id)
    ).first()


def _callback(client, world, user, **params):
    query = {"state": issue_gmail_state(world.project.id, user.id), "code": "auth-code", **params}
    return client.get("/integrations/gmail/callback", params=query, follow_redirects=False)


def test_owner_starts_gmail_connect(configured_gmail, client, world, login_as):
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/connect",
        data=_csrf(world.owner),
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(AUTH_URL)
    state = parse_qs(urlparse(location).query)["state"][0]
    assert read_gmail_state(state, world.owner.id) == world.project.id


def test_member_cannot_start_gmail_connect(configured_gmail, client, world, login_as):
    login_as(world.member.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/connect", data=_csrf(world.member)
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "form"),
    [
        ("get", "labels", None),
        ("post", "labels", {"label_ids": [], "all_label_ids": [], "types": [], "priorities": []}),
        ("post", "disconnect", {"confirm": "Disconnect Gmail"}),
    ],
)
def test_member_is_blocked_from_gmail_owner_routes(
    configured_gmail, client, world, login_as, session, method, path, form
):
    _connection(session, world)
    login_as(world.member.email)
    url = f"/projects/marketing/settings/integrations/gmail/{path}"
    if method == "get":
        response = client.get(url)
    else:
        response = client.post(url, data={**_csrf(world.member), **form})
    assert response.status_code == 403
    assert _stored(session, world) is not None


def test_connect_without_google_settings_is_unavailable(client, world, login_as):
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/connect", data=_csrf(world.owner)
    )
    assert response.status_code == 503


def test_callback_stores_encrypted_connection_and_opens_label_picker(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _use_google(monkeypatch)
    login_as(world.owner.email)
    response = _callback(client, world, world.owner)
    assert response.status_code == 303
    assert response.headers["location"] == "/projects/marketing/settings/integrations/gmail/labels"
    row = _stored(session, world)
    assert row.google_email == "marketing@example.com"
    assert row.history_id == "500"
    assert row.status == GmailConnectionStatus.ACTIVE
    assert row.user_id == world.owner.id
    assert "1//refresh" not in row.refresh_token_enc
    assert decrypt_token(row.refresh_token_enc) == "1//refresh"


def test_callback_rejects_state_from_another_user(configured_gmail, client, world, login_as):
    login_as(world.member.email)
    state = issue_gmail_state(world.project.id, world.owner.id)
    response = client.get("/integrations/gmail/callback", params={"state": state, "code": "c"})
    assert response.status_code == 403


def test_callback_requires_owner_role(configured_gmail, client, world, login_as, session):
    login_as(world.member.email)
    response = _callback(client, world, world.member)
    assert response.status_code == 403
    assert _stored(session, world) is None


def test_callback_access_denied_shows_message(configured_gmail, client, world, login_as, session):
    login_as(world.owner.email)
    response = _callback(client, world, world.owner, error="access_denied")
    assert response.headers["location"] == "/projects/marketing/settings?gmail_error=oauth_denied"
    assert _stored(session, world) is None
    assert "Gmail authorization was denied." in client.get(response.headers["location"]).text


def test_callback_google_failure_shows_message(
    configured_gmail, client, world, login_as, monkeypatch
):
    _use_google(monkeypatch, token_error="invalid_grant")
    login_as(world.owner.email)
    response = _callback(client, world, world.owner)
    assert response.headers["location"] == (
        "/projects/marketing/settings?gmail_error=verification_failed"
    )


def test_reconnect_same_mailbox_keeps_mapping_and_history(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(
        session,
        world,
        status=GmailConnectionStatus.NEEDS_REAUTH,
        last_error="Google access was revoked. Reconnect Gmail.",
        refresh_token_enc=encrypt_token("1//old"),
    )
    _use_google(monkeypatch)
    login_as(world.owner.email)
    _callback(client, world, world.owner)
    row = _stored(session, world)
    assert row.status == GmailConnectionStatus.ACTIVE
    assert row.last_error is None
    assert row.history_id == "100"
    assert row.label_mapping == {
        "Label_1": {"name": "Flyers", "type": "TASK", "priority": "MEDIUM"}
    }
    assert decrypt_token(row.refresh_token_enc) == "1//refresh"


def test_reconnect_different_mailbox_resets_mapping_and_history(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(session, world)
    _use_google(monkeypatch, email="events@example.com")
    login_as(world.owner.email)
    _callback(client, world, world.owner)
    row = _stored(session, world)
    assert row.google_email == "events@example.com"
    assert row.label_mapping == {}
    assert row.history_id == "500"


def test_label_picker_lists_only_user_labels(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(session, world)
    _use_google(monkeypatch)
    login_as(world.owner.email)
    response = client.get("/projects/marketing/settings/integrations/gmail/labels")
    assert response.status_code == 200
    assert "Flyers" in response.text
    assert "Urgent" in response.text
    assert 'value="INBOX"' not in response.text
    assert 'name="label_ids" value="Label_1" checked' in response.text


def test_label_picker_opens_in_a_dialog_with_mapped_labels_first(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(
        session,
        world,
        label_mapping={"Label_2": {"name": "Urgent", "type": "BUG", "priority": "URGENT"}},
    )
    _use_google(monkeypatch)
    login_as(world.owner.email)
    page = client.get("/projects/marketing/settings/integrations/gmail/labels").text
    dialog = page[page.index('<dialog id="gmail-labels-dialog"') :]
    assert 'type="search"' in dialog
    assert dialog.index("Urgent") < dialog.index("Flyers")
    assert 'name="label_ids" value="Label_2" checked' in dialog


def test_settings_page_without_label_picker_has_no_dialog(
    configured_gmail, client, world, login_as, session
):
    _connection(session, world)
    login_as(world.owner.email)
    page = client.get("/projects/marketing/settings").text
    assert "gmail-labels-dialog" not in page
    assert 'name="label_ids"' not in page


def test_save_labels_stores_mapping(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(session, world)
    _use_google(monkeypatch)
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/labels",
        data={
            **_csrf(world.owner),
            "label_ids": ["Label_2"],
            "all_label_ids": ["Label_1", "Label_2"],
            "types": ["TASK", "BUG"],
            "priorities": ["MEDIUM", "URGENT"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/projects/marketing/settings?saved=1"
    assert _stored(session, world).label_mapping == {
        "Label_2": {"name": "Urgent", "type": "BUG", "priority": "URGENT"}
    }


@pytest.mark.parametrize(
    "fields",
    [
        {
            "label_ids": ["Label_9"],
            "all_label_ids": ["Label_9"],
            "types": ["TASK"],
            "priorities": ["LOW"],
        },
        {
            "label_ids": ["Label_1"],
            "all_label_ids": ["Label_1"],
            "types": ["EPIC"],
            "priorities": ["LOW"],
        },
        {
            "label_ids": ["Label_1"],
            "all_label_ids": ["Label_1"],
            "types": ["TASK"],
            "priorities": [],
        },
        {
            "label_ids": ["Label_2"],
            "all_label_ids": ["Label_1"],
            "types": ["TASK"],
            "priorities": ["LOW"],
        },
    ],
)
def test_save_labels_rejects_invalid_mapping(
    configured_gmail, client, world, login_as, session, monkeypatch, fields
):
    _connection(session, world)
    _use_google(monkeypatch)
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/labels",
        data={**_csrf(world.owner), **fields},
    )
    assert response.status_code == 422
    assert _stored(session, world).label_mapping == {
        "Label_1": {"name": "Flyers", "type": "TASK", "priority": "MEDIUM"}
    }


def test_revoked_access_on_label_page_marks_reauth(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(session, world)
    _use_google(monkeypatch, token_error="invalid_grant")
    login_as(world.owner.email)
    response = client.get("/projects/marketing/settings/integrations/gmail/labels")
    assert response.status_code == 409
    assert "Reconnect Gmail" in response.text
    assert _stored(session, world).status == GmailConnectionStatus.NEEDS_REAUTH


def test_disconnect_revokes_and_deletes(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(session, world)
    requests = []
    _use_google(monkeypatch, requests=requests)
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/disconnect",
        data={**_csrf(world.owner), "confirm": "Disconnect Gmail"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert _stored(session, world) is None
    [revoke] = [r for r in requests if r.url.path == "/revoke"]
    assert parse_qs(revoke.content.decode()) == {"token": ["1//refresh"]}


def test_disconnect_keeps_google_grant_when_another_project_uses_the_mailbox(
    configured_gmail, client, world, login_as, session, monkeypatch, make_project
):
    _connection(session, world)
    other = make_project(world.owner, name="Events")
    session.add(
        GmailConnection(
            project_id=other.id,
            user_id=world.owner.id,
            google_email="marketing@example.com",
            refresh_token_enc=encrypt_token("1//other"),
            history_id="100",
        )
    )
    session.commit()
    requests = []
    _use_google(monkeypatch, requests=requests)
    login_as(world.owner.email)
    client.post(
        "/projects/marketing/settings/integrations/gmail/disconnect",
        data={**_csrf(world.owner), "confirm": "Disconnect Gmail"},
    )
    assert _stored(session, world) is None
    assert [r for r in requests if r.url.path == "/revoke"] == []


def test_disconnect_still_deletes_when_revoke_fails(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(session, world)
    _use_google(monkeypatch, revoke_status=400)
    login_as(world.owner.email)
    client.post(
        "/projects/marketing/settings/integrations/gmail/disconnect",
        data={**_csrf(world.owner), "confirm": "Disconnect Gmail"},
    )
    assert _stored(session, world) is None


def test_disconnect_requires_confirmation(configured_gmail, client, world, login_as, session):
    _connection(session, world)
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/disconnect",
        data={**_csrf(world.owner), "confirm": "yes"},
    )
    assert response.status_code == 422
    assert _stored(session, world) is not None


def test_settings_card_shows_mapping_to_owner_and_status_to_member(
    configured_gmail, client, world, login_as, session
):
    _connection(session, world)
    login_as(world.owner.email)
    owner_page = client.get("/projects/marketing/settings").text
    assert "marketing@example.com" in owner_page
    assert "Flyers → Task · Medium" in owner_page
    assert "Edit labels" in owner_page

    login_as(world.member.email)
    member_page = client.get("/projects/marketing/settings").text
    assert "Gmail" in member_page
    assert "marketing@example.com" not in member_page


def test_settings_card_offers_reconnect_when_access_was_revoked(
    configured_gmail, client, world, login_as, session
):
    _connection(
        session,
        world,
        status=GmailConnectionStatus.NEEDS_REAUTH,
        last_error="Google access was revoked. Reconnect Gmail.",
    )
    login_as(world.owner.email)
    page = client.get("/projects/marketing/settings").text
    assert "Reconnect required" in page
    assert "Reconnect Gmail" in page
