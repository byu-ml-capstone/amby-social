from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

from app.config import settings

PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
)


def make_engine(database_url: str) -> Engine:
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _apply_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        for pragma in PRAGMAS:
            cursor.execute(pragma)
        cursor.close()

    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine(settings.database_url)


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def begin_write(session: Session) -> None:
    """Take SQLite's write lock before a check-then-write.

    pysqlite opens a transaction only at the first INSERT/UPDATE/DELETE, so the reads that
    decide a write run outside any transaction and another writer can commit in between.
    BEGIN IMMEDIATE waits (busy_timeout) for the lock first, so those reads see final data.
    """
    connection = session.connection()
    if (
        connection.dialect.name == "sqlite"
        and not connection.connection.dbapi_connection.in_transaction
    ):
        connection.exec_driver_sql("BEGIN IMMEDIATE")
