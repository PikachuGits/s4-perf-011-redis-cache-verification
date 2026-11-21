#!/usr/bin/env python3
"""缓存开/关对比压测：固定请求组合、预热、应用层与 PostgreSQL 侧同时采样。"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import psycopg

from app.demo import DICT_CODES, PERIODS, SITE_B, SITE_C, SITE_MAIN, USER_IDS

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "dev-admin-token")

WEIGHTS = {
    "home": 40,
    "dict": 25,
    "dashboard": 20,
    "perms": 15,
}


@dataclass
class Sample:
    endpoint: str
    latency_ms: float
    status: int
    db_queries: int
    db_time_ms: float
    cache_state: str
    valid: bool


@dataclass
class Phase:
    samples: list[Sample] = field(default_factory=list)
    errors: int = 0
    started_at: str = ""
    finished_at: str = ""
    wall_seconds: float = 0.0


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def _summarize(samples: list[Sample]) -> dict[str, Any]:
    if not samples:
        return {}
    latencies = [s.latency_ms for s in samples]
    return {
        "count": len(samples),
        "ok": sum(1 for s in samples if 200 <= s.status < 300 and s.valid),
        "errors": sum(1 for s in samples if not (200 <= s.status < 300 and s.valid)),
        "latency_ms": {
            "min": round(min(latencies), 3),
            "p50": round(_percentile(latencies, 50), 3),
            "p95": round(_percentile(latencies, 95), 3),
            "p99": round(_percentile(latencies, 99), 3),
            "max": round(max(latencies), 3),
            "mean": round(statistics.fmean(latencies), 3),
        },
        "db_queries_total": sum(s.db_queries for s in samples),
        "db_queries_per_request": round(
            sum(s.db_queries for s in samples) / len(samples), 3
        ),
        "db_time_ms_per_request": round(
            sum(s.db_time_ms for s in samples) / len(samples), 3
        ),
        "cache_states": {
            state: sum(1 for s in samples if s.cache_state == state)
            for state in sorted({s.cache_state for s in samples})
        },
    }


def _pg_snapshot(dsn: str) -> dict[str, Any]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT xact_commit, xact_rollback, blks_read, blks_hit,
                       tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted
                FROM pg_stat_database WHERE datname = current_database()
                """
            )
            columns = [desc.name for desc in cur.description]
            db_row = dict(zip(columns, cur.fetchone(), strict=False))
            cur.execute(
                """
                SELECT relname, seq_scan, idx_scan, n_tup_ins, n_tup_upd, n_tup_del
                FROM pg_stat_user_tables WHERE schemaname = 'public'
                """
            )
            tables = {
                row[0]: {
                    "seq_scan": row[1],
                    "idx_scan": row[2],
                    "n_tup_ins": row[3],
                    "n_tup_upd": row[4],
                    "n_tup_del": row[5],
                }
                for row in cur.fetchall()
            }
    return {"database": db_row, "tables": tables, "at": datetime.now(timezone.utc).isoformat()}


def _pg_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    db = {
        key: after["database"].get(key, 0) - before["database"].get(key, 0)
        for key in (
            "xact_commit", "xact_rollback", "blks_read", "blks_hit",
            "tup_returned", "tup_fetched", "tup_inserted", "tup_updated", "tup_deleted",
        )
    }
    tables: dict[str, Any] = {}
    for name, after_row in after["tables"].items():
        before_row = before["tables"].get(name, {})
        delta = {
            key: after_row.get(key, 0) - before_row.get(key, 0)
            for key in ("seq_scan", "idx_scan", "n_tup_ins", "n_tup_upd", "n_tup_del")
        }
        if any(delta.values()):
            tables[name] = delta
    return {"database": db, "tables": tables}


def _request_specs(rng: random.Random, requests: int) -> list[tuple[str, str, dict[str, str], str]]:
    specs: list[tuple[str, str, dict[str, str], str]] = []
    endpoint_names = list(WEIGHTS)
    for _ in range(requests):
        endpoint = rng.choices(endpoint_names, weights=[WEIGHTS[name] for name in endpoint_names])[0]
        if endpoint == "home":
            site = rng.choices([SITE_MAIN, SITE_B, SITE_C], weights=[80, 10, 10])[0]
            group = "main" if site == SITE_MAIN else "other"
            specs.append((endpoint, "/api/site/home", {"X-Site-Id": str(site)}, group))
        elif endpoint == "dict":
            code = rng.choices(list(DICT_CODES), weights=[50, 30, 20])[0]
            specs.append(
                (endpoint, f"/api/tax/dicts/{code}/tree", {}, code)
            )
        elif endpoint == "dashboard":
            site = rng.choices([SITE_MAIN, SITE_B, SITE_C], weights=[80, 10, 10])[0]
            period = rng.choice(PERIODS)
            specs.append(
                (
                    endpoint,
                    f"/api/dashboard/metrics?period={period}",
                    {"X-Site-Id": str(site)},
                    "main" if site == SITE_MAIN else "other",
                )
            )
        else:
            user = rng.choice(USER_IDS)
            specs.append(
                (
                    endpoint,
                    "/api/iam/permissions",
                    {"X-User-Id": str(user)},
                    "user",
                )
            )
    return specs


def _warmup_specs() -> list[tuple[str, str, dict[str, str], str]]:
    specs: list[tuple[str, str, dict[str, str], str]] = []
    for site in (SITE_MAIN, SITE_B, SITE_C):
        specs.append(("home", "/api/site/home", {"X-Site-Id": str(site)}, "main"))
    for code in DICT_CODES:
        specs.append(("dict", f"/api/tax/dicts/{code}/tree", {}, code))
    for site in (SITE_MAIN, SITE_B, SITE_C):
        for period in PERIODS:
            specs.append(
                ("dashboard", f"/api/dashboard/metrics?period={period}", {"X-Site-Id": str(site)}, "main")
            )
    for user in USER_IDS:
        specs.append(("perms", "/api/iam/permissions", {"X-User-Id": str(user)}, "user"))
    return specs


def _valid(endpoint: str, body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    if endpoint == "home":
        return "counts" in body and "banners" in body
    if endpoint == "dict":
        return "tree" in body and "dict_code" in body
    if endpoint == "dashboard":
        return "opportunities" in body and "receivables" in body
    if endpoint == "perms":
        return "permissions" in body and "roles" in body
    return True


async def _run_phase(
    base_url: str,
    specs: list[tuple[str, str, dict[str, str], str]],
    concurrency: int,
    timeout: float,
) -> Phase:
    phase = Phase()
    queue: asyncio.Queue = asyncio.Queue()
    for spec in specs:
        queue.put_nowait(spec)
    samples: list[Sample] = []

    limits = httpx.Limits(
        max_connections=concurrency + 4,
        max_keepalive_connections=concurrency + 4,
    )
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, limits=limits) as client:

        async def worker() -> None:
            while True:
                try:
                    endpoint, path, headers, _group = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                started = time.perf_counter_ns()
                status = 0
                db_queries = 0
                db_time = 0.0
                cache_state = "none"
                valid = False
                try:
                    response = await client.get(path, headers=headers)
                    status = response.status_code
                    db_queries = int(response.headers.get("X-DB-Queries", "0"))
                    db_time = float(response.headers.get("X-DB-Time-Ms", "0"))
                    cache_state = response.headers.get("X-Cache-State", "none")
                    try:
                        body = response.json()
                    except Exception:
                        body = None
                    valid = 200 <= status < 300 and _valid(endpoint, body)
                except Exception:
                    valid = False
                latency_ms = (time.perf_counter_ns() - started) / 1_000_000
                samples.append(
                    Sample(endpoint, latency_ms, status, db_queries, db_time, cache_state, valid)
                )
                queue.task_done()

        phase.started_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await asyncio.gather(*workers)
        phase.wall_seconds = round(time.perf_counter() - started, 3)
        phase.finished_at = datetime.now(timezone.utc).isoformat()

    phase.samples = samples
    phase.errors = sum(1 for sample in samples if not (200 <= sample.status < 300 and sample.valid))
    return phase


def _endpoint_breakdown(samples: list[Sample], wall_seconds: float) -> dict[str, Any]:
    grouped: dict[str, list[Sample]] = {}
    for sample in samples:
        grouped.setdefault(sample.endpoint, []).append(sample)
    result: dict[str, Any] = {}
    for endpoint, items in grouped.items():
        summary = _summarize(items)
        summary["rps"] = round(len(items) / wall_seconds, 2) if wall_seconds else 0
        result[endpoint] = summary
    return result


async def main_async(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    admin_headers = {"X-Admin-Token": ADMIN_TOKEN}
    dsn = args.db_url.replace("postgresql+psycopg://", "postgresql://")

    async with httpx.AsyncClient(base_url=args.base_url, timeout=10.0) as client:
        health = await client.get("/healthz")
        print(f"[loadtest] health={health.status_code} {health.json()}")

        warm = await _run_phase(
            args.base_url, _warmup_specs(), min(args.concurrency, 16), args.timeout
        )
        print(f"[loadtest] warmup requests={len(warm.samples)} errors={warm.errors}")
        await client.post("/api/admin/cache/reset-stats", headers=admin_headers)

    pg_before = _pg_snapshot(dsn)
    phase = await _run_phase(
        args.base_url, _request_specs(rng, args.requests), args.concurrency, args.timeout
    )
    pg_after = _pg_snapshot(dsn)

    summary = _summarize(phase.samples)
    summary["rps"] = round(len(phase.samples) / phase.wall_seconds, 2) if phase.wall_seconds else 0
    summary["wall_seconds"] = phase.wall_seconds
    endpoints = _endpoint_breakdown(phase.samples, phase.wall_seconds)

    async with httpx.AsyncClient(base_url=args.base_url, timeout=10.0) as client:
        cache_stats = (await client.get("/api/admin/cache/stats", headers=admin_headers)).json()
        db_stats = (await client.get("/api/admin/db/stats", headers=admin_headers)).json()

    payload = {
        "label": args.label,
        "mode": args.mode,
        "base_url": args.base_url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "requests": args.requests,
            "concurrency": args.concurrency,
            "timeout_seconds": args.timeout,
            "seed": args.seed,
            "weights": WEIGHTS,
            "warmup_requests": len(warm.samples),
        },
        "phase": {
            "started_at": phase.started_at,
            "finished_at": phase.finished_at,
            "wall_seconds": phase.wall_seconds,
            "errors": phase.errors,
        },
        "summary": summary,
        "by_endpoint": endpoints,
        "cache": cache_stats,
        "app_db": db_stats,
        "postgres_before": pg_before,
        "postgres_after": pg_after,
        "postgres_delta": _pg_delta(pg_before, pg_after),
        "samples": [
            {
                "endpoint": sample.endpoint,
                "latency_ms": round(sample.latency_ms, 3),
                "status": sample.status,
                "db_queries": sample.db_queries,
                "db_time_ms": round(sample.db_time_ms, 3),
                "cache_state": sample.cache_state,
                "valid": sample.valid,
            }
            for sample in phase.samples
        ],
    }

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[loadtest] {args.label} mode={args.mode} requests={summary['count']} "
        f"errors={summary['errors']} rps={summary['rps']} "
        f"p50={summary['latency_ms']['p50']} p95={summary['latency_ms']['p95']} "
        f"p99={summary['latency_ms']['p99']} db_q/req={summary['db_queries_per_request']}"
    )
    print(f"[loadtest] wrote {output}")
    return 0 if phase.errors == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--label", required=True)
    parser.add_argument("--mode", choices=["cache-on", "cache-off"], required=True)
    parser.add_argument("--requests", type=int, default=8000)
    parser.add_argument("--concurrency", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--seed", type=int, default=20261121)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--db-url",
        default="postgresql://clawhunt@127.0.0.1:55432/clawhunt_2984",
    )
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
