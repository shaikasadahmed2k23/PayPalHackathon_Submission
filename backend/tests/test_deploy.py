"""Deployment plumbing: DB URL normalisation, CORS parsing, production guard, /health."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings, check_production_settings
from app.db import make_engine, normalize_database_url


@pytest.mark.parametrize("raw,expected", [
    ("postgresql://u:p@h:5432/postgres", "postgresql+psycopg://u:p@h:5432/postgres"),
    ("postgres://u:p@h:5432/postgres", "postgresql+psycopg://u:p@h:5432/postgres"),
    ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ("sqlite:///./x.db", "sqlite:///./x.db"),
])
def test_normalize_database_url(raw, expected):
    assert normalize_database_url(raw) == expected


def test_postgres_engine_is_lazy_and_pooled_small():
    e = make_engine("postgres://u:p@localhost:5432/db")  # no connection until first use
    assert e.dialect.name == "postgresql" and e.dialect.driver == "psycopg"
    assert e.pool.size() == 5 and e.pool._max_overflow == 5 and e.pool._pre_ping


def test_cors_origins_parsing():
    s = Settings(cors_origins=" https://a.onrender.com/ ,http://localhost:3000,, ")
    assert s.cors_origin_list == ["https://a.onrender.com", "http://localhost:3000"]
    assert Settings().cors_origin_list == ["http://localhost:3000"]


def test_production_requires_signing_key():
    with pytest.raises(RuntimeError, match="SERVER_SIGNING_KEY"):
        check_production_settings(Settings(app_env="production", server_signing_key=""))
    check_production_settings(Settings(app_env="production", server_signing_key="abc"))
    check_production_settings(Settings(app_env="development", server_signing_key=""))


def test_health_reports_dialect_not_url(monkeypatch):
    from app import db

    local = make_engine("sqlite://")  # hermetic: ignore whatever DATABASE_URL backend/.env holds
    monkeypatch.setattr(db, "engine", local)
    r = TestClient(main.app).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "sqlite"}


def test_health_503_when_db_down(monkeypatch):
    def boom() -> str:
        raise RuntimeError("password=secret")

    monkeypatch.setattr(main, "check_db", boom)
    r = TestClient(main.app).get("/health")
    assert r.status_code == 503 and "secret" not in r.text
