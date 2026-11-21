from __future__ import annotations

import time
from typing import Annotated

from fastapi import FastAPI, Header, Request
from sqlalchemy import text

from app.cache import cache, current_cache_state, new_request_state
from app.config import get_settings
from app.db import SessionLocal, current_request_metrics, start_request_metrics
from app.routers import admin, dashboard, home, iam, tax


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="会员空间平台 · Redis 缓存策略验证",
        version="1.0.0",
        description="S4-PERF-011 缓存点、开关缓存对比、失效时延与穿透/击穿防护验证服务（虚构数据）。",
    )

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        metrics = start_request_metrics()
        cache_state = new_request_state()
        started = time.perf_counter_ns()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        response.headers["X-Cache-Enabled"] = "1" if cache.enabled else "0"
        response.headers["X-DB-Queries"] = str(metrics.queries)
        response.headers["X-DB-Time-Ms"] = f"{metrics.query_time_ns / 1_000_000:.3f}"
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.3f}"
        if cache_state.point:
            response.headers.setdefault("X-Cache-Point", cache_state.point)
        if cache_state.state:
            response.headers["X-Cache-State"] = cache_state.state
        return response

    app.include_router(tax.router)
    app.include_router(home.router)
    app.include_router(dashboard.router)
    app.include_router(iam.router)
    app.include_router(admin.router)

    @app.get("/healthz", tags=["meta"])
    def healthz():
        db_ok = False
        try:
            with SessionLocal() as session:
                session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False
        return {
            "ok": db_ok and cache.ping(),
            "db": db_ok,
            "redis": cache.ping(),
            "cache_enabled": cache.enabled,
        }

    @app.get("/", tags=["meta"])
    def root():
        return {
            "service": "member-space-redis-cache-verification",
            "task": "S4-PERF-011",
            "cache_enabled": cache.enabled,
            "docs": "/docs",
            "health": "/healthz",
        }

    @app.get("/api/meta/request", tags=["meta"])
    def request_meta(
        request: Request,
        x_debug: Annotated[str | None, Header(alias="X-Debug")] = None,
    ):
        metrics = current_request_metrics()
        state = current_cache_state()
        return {
            "cache_enabled": cache.enabled,
            "cache_state": state.state if state else None,
            "cache_point": state.point if state else None,
            "db_queries_so_far": metrics.queries if metrics else 0,
            "settings_ttl_scale": settings.cache_ttl_scale,
        }

    return app


app = create_app()
