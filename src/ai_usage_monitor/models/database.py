from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class ProviderStateRecord(Base):
    __tablename__ = "provider_state"

    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    successful_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(String(100), nullable=True)


class SnapshotRecord(Base):
    __tablename__ = "usage_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    quota_type: Mapped[str] = mapped_column(String(120), index=True)
    scope: Mapped[str | None] = mapped_column(String(160), nullable=True)
    used_percent: Mapped[float] = mapped_column(Float, nullable=False)
    reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)


def create_database(database_url: str):
    url = make_url(database_url)
    memory = url.database == ":memory:"
    if not memory and url.database:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    connect_args = (
        {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    )
    engine = create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        **({"poolclass": StaticPool} if memory else {}),
    )

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)
