from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parents[1]

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://clawhunt@127.0.0.1:55432/clawhunt_2984_test",
)
if "test" not in TEST_DATABASE_URL.rsplit("/", 1)[-1]:
    raise RuntimeError("TEST_DATABASE_URL 必须指向库名含 test 的专用库")

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:56379/15")
os.environ["CACHE_ENABLED"] = "1"
os.environ["ADMIN_TOKEN"] = "test-admin-token"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.cache import cache  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, engine, global_metrics  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.seed import seed_test_data  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def schema() -> Iterator[None]:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def clean_state() -> Iterator[None]:
    table_names = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    cache.redis.flushdb()
    cache.reset_stats()
    cache.enabled = True
    global_metrics.reset()
    with SessionLocal() as session:
        seed_test_data(session)
        session.commit()
    yield
    cache.enabled = True


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": "test-admin-token"}


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
