from collections.abc import Iterator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  (registers tables on SQLModel.metadata)
from app.config import get_settings

_url = get_settings().database_url
# check_same_thread=False: FastAPI runs sync endpoints in a threadpool.
_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}
engine = create_engine(_url, connect_args=_connect_args)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """create_all never alters existing tables; top up the few columns added after Task 5."""
    if "created_at" not in {c["name"] for c in inspect(engine).get_columns("purchase")}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE purchase ADD COLUMN created_at TIMESTAMP"))


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
