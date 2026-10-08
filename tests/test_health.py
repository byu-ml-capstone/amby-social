from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_fails_when_database_is_unreachable(tmp_path):
    from sqlmodel import Session

    from app.db import get_session, make_engine

    engine = make_engine(f"sqlite:///{tmp_path / 'missing-dir' / 'db.sqlite'}")

    def broken_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = broken_session
    try:
        response = TestClient(app, raise_server_exceptions=False).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 500
