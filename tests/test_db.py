from sqlalchemy import text


def test_pragmas_are_applied_to_every_connection(engine):
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 1


def test_sqlite_supports_returning(engine):
    with engine.connect() as conn:
        version = conn.execute(text("select sqlite_version()")).scalar()
    major, minor, *_ = (int(p) for p in version.split("."))
    assert (major, minor) >= (3, 35), f"RETURNING needs SQLite 3.35+, found {version}"
