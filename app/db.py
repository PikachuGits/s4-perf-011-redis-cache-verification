from __future__ import annotations

import contextvars
import threading
import time
from dataclasses import dataclass

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_recycle=_settings.db_pool_recycle,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@dataclass
class RequestDBMetrics:
    queries: int = 0
    query_time_ns: int = 0


_current: contextvars.ContextVar[RequestDBMetrics | None] = contextvars.ContextVar(
    "request_db_metrics", default=None
)


class GlobalMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.requests = 0
            self.db_queries = 0
            self.db_time_ns = 0

    def add_request(self) -> None:
        with self._lock:
            self.requests += 1

    def add_query(self, duration_ns: int) -> None:
        with self._lock:
            self.db_queries += 1
            self.db_time_ns += duration_ns

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            return {
                "requests": self.requests,
                "db_queries": self.db_queries,
                "db_time_ms": round(self.db_time_ns / 1_000_000, 3),
                "db_queries_per_request": round(self.db_queries / self.requests, 3)
                if self.requests
                else 0.0,
                "db_time_ms_per_request": round(self.db_time_ns / 1_000_000 / self.requests, 3)
                if self.requests
                else 0.0,
            }


global_metrics = GlobalMetrics()


def start_request_metrics() -> RequestDBMetrics:
    metrics = RequestDBMetrics()
    _current.set(metrics)
    global_metrics.add_request()
    return metrics


def current_request_metrics() -> RequestDBMetrics | None:
    return _current.get()


@event.listens_for(engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    conn.info.setdefault("_query_start_ns", []).append(time.perf_counter_ns())


@event.listens_for(engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    stack = conn.info.get("_query_start_ns") or []
    if not stack:
        return
    started = stack.pop()
    duration = time.perf_counter_ns() - started
    metrics = _current.get()
    if metrics is not None:
        metrics.queries += 1
        metrics.query_time_ns += duration
    global_metrics.add_query(duration)


def get_db():
    session: Session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
