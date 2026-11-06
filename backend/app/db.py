from collections.abc import Iterator

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  (registers tables on SQLModel.metadata)
from app.config import get_settings


def normalize_database_url(url: str) -> str:
    """Accept postgres:// and postgresql:// (Supabase/Heroku style) and select the psycopg v3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url: str) -> Engine:
    url = normalize_database_url(url)
    if url.startswith("sqlite"):
        # check_same_thread=False: FastAPI runs sync endpoints in a threadpool.
        return create_engine(url, connect_args={"check_same_thread": False})
    # Small pool: Supabase's pooler has a low connection cap. pre_ping drops connections it closed while idle.
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


engine = make_engine(get_settings().database_url)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()
    if engine.dialect.name != "sqlite":
        _add_missing_enum_values()


def _add_missing_columns() -> None:
    """create_all never alters existing tables; top up the columns added after the first deploy."""
    if engine.dialect.name == "sqlite":
        def have(table: str) -> set[str]:
            return {c["name"] for c in inspect(engine).get_columns(table)}

        with engine.begin() as conn:
            if "created_at" not in have("purchase"):
                conn.execute(text("ALTER TABLE purchase ADD COLUMN created_at TIMESTAMP"))
            if "owner_token_hash" not in have("allowance"):
                conn.execute(text("ALTER TABLE allowance ADD COLUMN owner_token_hash VARCHAR"))
    else:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE allowance ADD COLUMN IF NOT EXISTS owner_token_hash VARCHAR"))


def _add_missing_enum_values() -> None:
    """create_all never alters an existing Postgres ENUM; add the labels introduced after first deploy."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_type WHERE typname = 'actor'")).first()
        if exists:
            conn.execute(text("ALTER TYPE actor ADD VALUE IF NOT EXISTS 'EXTERNAL_AGENT'"))


def check_db() -> str:
    """Round-trip a trivial query; returns the dialect name (sqlite | postgresql). Never exposes the URL."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return engine.dialect.name


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
