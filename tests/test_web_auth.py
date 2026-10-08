from sqlmodel import select

from app.auth import SESSION_COOKIE, make_csrf_token
from app.main import templates
from app.models import User


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "<form" in response.text


def test_auth_pages_share_the_application_form_surface(client):
    """Replacing the shared auth surface with utility classes must make this fail."""
    for path in ("/login", "/register"):
        response = client.get(path)

        assert 'class="auth-page"' in response.text
        assert 'class="auth-card app-surface"' in response.text
        assert 'class="app-form"' in response.text
        assert 'class="app-input"' in response.text
        assert 'class="app-primary-button auth-submit"' in response.text


def test_register_form_creates_a_session_and_redirects(client):
    response = client.post(
        "/register",
        data={"name": "Ada", "email": "ada@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert SESSION_COOKIE in response.cookies


def test_duplicate_registration_rerenders_with_an_error(client, make_user):
    make_user(email="ada@example.com")
    response = client.post(
        "/register",
        data={"name": "Ada", "email": "ada@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert "already registered" in response.text.lower()


def test_bad_login_rerenders_with_an_error(client, make_user):
    make_user(email="ada@example.com")
    response = client.post(
        "/login", data={"email": "ada@example.com", "password": "wrong"}, follow_redirects=False
    )
    assert response.status_code == 401
    assert "invalid" in response.text.lower()


def test_unknown_email_login_still_runs_verify_password(client, monkeypatch):
    # Constant-time requirement: api_auth.login runs verify_password against a real hash
    # (DUMMY_HASH) even when the email doesn't exist, so bcrypt's cost lands on both branches
    # and a non-existent account isn't distinguishable from a wrong password by timing. This
    # asserts the behaviour (verify_password is actually invoked, against DUMMY_HASH) rather
    # than the wall-clock, which HTTP-level timing is too noisy to assert reliably.
    import app.routers.web as web

    calls = []
    original_verify_password = web.verify_password

    def spy(password, hashed):
        calls.append(hashed)
        return original_verify_password(password, hashed)

    monkeypatch.setattr(web, "verify_password", spy)

    response = client.post(
        "/login",
        data={"email": "nobody@example.com", "password": "whatever"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert calls == [web.DUMMY_HASH]


def test_register_rejects_a_malformed_email(client):
    response = client.post(
        "/register",
        data={"name": "Ada", "email": "not-an-email", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "valid email" in response.text.lower()


def test_register_strips_and_validates_name(client, session):
    response = client.post(
        "/register",
        data={"name": "  Ada  ", "email": "ada@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert session.exec(select(User).where(User.email == "ada@example.com")).one().name == "Ada"

    response = client.post(
        "/register",
        data={"name": "   ", "email": "grace@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "blank" in response.text.lower()

    response = client.post(
        "/register",
        data={"name": "a" * 51, "email": "lin@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "50 characters" in response.text


def test_overlong_password_rerenders_with_an_error_instead_of_crashing(client):
    # bcrypt (via hash_password) raises ValueError past 72 UTF-8 bytes. The JSON route is
    # protected by RegisterRequest's validator; this form-based route has no schema in front
    # of it, so it needs its own guard or an over-length password would 500 instead of 422.
    response = client.post(
        "/register",
        data={"name": "Ada", "email": "ada@example.com", "password": "a" * 73},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "72 bytes" in response.text


def test_root_redirects_anonymous_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_logged_in_user_visiting_login_is_redirected_to_dashboard(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_logged_in_user_visiting_register_is_redirected_to_dashboard(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    response = client.get("/register", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_logout_clears_the_session_cookie(client, make_user, login_as):
    user = make_user(email="ada@example.com")
    login_as("ada@example.com")
    assert client.cookies.get(SESSION_COOKIE) is not None

    response = client.post(
        "/logout", data={"_csrf": make_csrf_token(user.id)}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    # httpx's cookie jar drops a cookie whose Set-Cookie response has already expired.
    assert client.cookies.get(SESSION_COOKIE) is None


def test_logout_rejects_a_missing_csrf_token(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 403


def test_markdown_filter_is_registered_on_the_templates_environment_and_renders():
    # A dict-membership check ("markdown" in templates.env.filters) would pass even if the
    # filter were registered on a different Environment than the one TemplateResponse actually
    # uses. Rendering through templates.env itself proves it reaches real template output.
    # autoescape is on, so | safe is required after | markdown -- exactly how a real
    # template would use it (e.g. `{{ ticket.description | markdown | safe }}`).
    rendered = templates.env.from_string("{{ '**bold**' | markdown | safe }}").render()
    assert "<strong>bold</strong>" in rendered
