import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.auth import hash_password
from app.db import get_session, make_engine
from app.main import app
from app.models import ProjectMember, Role, User
from app.services import create_project


@pytest.fixture(autouse=True)
def isolate_security_state(monkeypatch):
    import socket

    from app.config import settings
    from app.security import _auth_attempts

    original_getaddrinfo = socket.getaddrinfo

    def stable_test_dns(host, *args, **kwargs):
        if host == "example.com" or str(host).endswith(".example.test"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        return original_getaddrinfo(host, *args, **kwargs)

    _auth_attempts.clear()
    monkeypatch.setattr(
        settings,
        "webhook_allowed_hosts",
        [*settings.webhook_allowed_hosts, "example.com", "example.test"],
    )
    monkeypatch.setattr(socket, "getaddrinfo", stable_test_dns)
    # A developer's .env may configure Gmail; tests opt in with their own fixtures instead.
    for name in (
        "google_client_id",
        "google_client_secret",
        "google_redirect_uri",
        "token_encryption_key",
    ):
        monkeypatch.setattr(settings, name, None)
    yield
    _auth_attempts.clear()


@pytest.fixture(autouse=True)
def isolate_attachments(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "attachments_dir", str(tmp_path / "attachments"))


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine):
    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def make_user(engine):
    def _make(email: str = "user@example.com", password: str = "hunter22", name: str = "User"):
        with Session(engine) as session:
            user = User(name=name, email=email.lower(), password_hash=hash_password(password))
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    return _make


@pytest.fixture
def make_project(engine):
    def _make(owner, name: str = "Payment Gateway"):
        with Session(engine) as session:
            return create_project(session, name, owner)

    return _make


@pytest.fixture
def add_member(engine):
    def _add(project, user, role: Role = Role.MEMBER):
        with Session(engine) as session:
            session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
            session.commit()

    return _add


@pytest.fixture
def login_as(client):
    def _login(email: str, password: str = "hunter22"):
        response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return response

    return _login
