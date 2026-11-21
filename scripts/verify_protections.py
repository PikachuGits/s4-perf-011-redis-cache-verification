#!/usr/bin/env python3
"""穿透与击穿防护的端到端对照：缓存开/关两套进程 + 真实并发。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import redis

from app.cache import get_point
from app.demo import CURRENT_PERIOD, ORG_ID, SITE_MAIN


async def _parallel_get(
    base_url: str, path: str, count: int, concurrency: int, headers: dict[str, str] | None = None
) -> list[tuple[int, dict[str, str], dict]]:
    headers = headers or {}
    semaphore = asyncio.Semaphore(concurrency)
    results: list[tuple[int, dict[str, str], dict]] = []

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=10.0,
        limits=httpx.Limits(max_connections=concurrency + 4, max_keepalive_connections=concurrency + 4),
    ) as client:

        async def one() -> None:
            async with semaphore:
                response = await client.get(path, headers=headers)
                try:
                    body = response.json()
                except Exception:
                    body = {}
                results.append((response.status_code, dict(response.headers), body))

        await asyncio.gather(*[one() for _ in range(count)])
    return results


def _sum_db(results, status: int | None = None) -> int:
    return sum(
        int(headers.get("x-db-queries", "0"))
        for code, headers, _body in results
        if status is None or code == status
    )


def _count_header(results, key: str, value: str) -> int:
    return sum(1 for _code, headers, _body in results if headers.get(key, "").lower() == value)


def _count_guard(results, guard: str) -> int:
    return sum(1 for _code, _headers, body in results if body.get("detail", {}).get("guard") == guard)


async def _reset_stats(base_url: str, headers: dict[str, str]) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
        await client.post("/api/admin/cache/reset-stats", headers=headers)


async def main_async(args: argparse.Namespace) -> int:
    admin_headers = {"X-Admin-Token": args.admin_token}
    redis_client = redis.Redis.from_url(args.redis_url, decode_responses=True)
    output: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_on_url": args.on_url,
        "cache_off_url": args.off_url,
    }

    async with httpx.AsyncClient(timeout=5.0) as client:
        on_health = (await client.get(f"{args.on_url}/healthz")).json()
        off_health = (await client.get(f"{args.off_url}/healthz")).json()
        print(f"[protections] on={on_health} off={off_health}")
    await _reset_stats(args.on_url, admin_headers)
    await _reset_stats(args.off_url, admin_headers)

    # 1) 预热 Bloom：一次正常字典项查询
    warm = await _parallel_get(
        args.on_url, "/api/tax/dicts/industry/items/IND-01-01", 1, 1
    )
    assert warm[0][0] == 200

    # 2) 穿透防护：不存在的字典项 × N
    unknown_on = await _parallel_get(
        args.on_url, "/api/tax/dicts/industry/items/NOT-EXIST-9999", args.penetration_requests, 20
    )
    unknown_off = await _parallel_get(
        args.off_url, "/api/tax/dicts/industry/items/NOT-EXIST-9999", args.penetration_off_requests, 20
    )
    output["penetration"] = {
        "unknown_item_same_key": True,
        "cache_on": {
            "requests": len(unknown_on),
            "status_404": sum(1 for code, _h, _b in unknown_on if code == 404),
            "db_queries_total": _sum_db(unknown_on),
            "bloom_guard_hits": _count_guard(unknown_on, "bloom"),
            "negative_guard_hits": _count_guard(unknown_on, "negative_cache"),
        },
        "cache_off": {
            "requests": len(unknown_off),
            "status_404": sum(1 for code, _h, _b in unknown_off if code == 404),
            "db_queries_total": _sum_db(unknown_off),
        },
    }

    # 3) 缺失字典的负缓存：连续两次
    async with httpx.AsyncClient(base_url=args.on_url, timeout=5.0) as client:
        first = await client.get("/api/tax/dicts/no_such_dict/tree")
        second = await client.get("/api/tax/dicts/no_such_dict/tree")
    output["negative_cache"] = {
        "first_db_queries": int(first.headers.get("X-DB-Queries", "0")),
        "second_db_queries": int(second.headers.get("X-DB-Queries", "0")),
        "second_state": second.headers.get("X-Cache-State"),
        "ttl_seconds": redis_client.ttl(
            get_point("tax.dict.tree").key(org_id=str(ORG_ID), dict_code="no_such_dict")
        ),
    }

    # 4) 击穿防护：删除热点键后并发读同一个驾驶舱指标
    point = get_point("dashboard.metrics")
    params = {"org_id": str(ORG_ID), "site_id": str(SITE_MAIN), "period": CURRENT_PERIOD}
    path = f"/api/dashboard/metrics?period={CURRENT_PERIOD}"
    redis_client.delete(point.key(**params))
    await _reset_stats(args.on_url, admin_headers)
    burst_on = await _parallel_get(args.on_url, path, args.burst_requests, args.burst_concurrency)

    await _reset_stats(args.off_url, admin_headers)
    big_burst = await _parallel_get(
        args.off_url, path, args.burst_requests, args.burst_concurrency
    )
    output["stampede"] = {
        "cache_on": {
            "requests": len(burst_on),
            "ok": sum(1 for code, _h, _b in burst_on if code == 200),
            "db_queries_total": _sum_db(burst_on),
        },
        "cache_off": {
            "requests": len(big_burst),
            "ok": sum(1 for code, _h, _b in big_burst if code == 200),
            "db_queries_total": _sum_db(big_burst),
        },
        "key_ttl_after_first_read": redis_client.ttl(point.key(**params)),
    }

    # 5) 取缓存统计（包含 loads/lock_waits）
    async with httpx.AsyncClient(base_url=args.on_url, timeout=5.0) as client:
        stats = (await client.get("/api/admin/cache/stats", headers=admin_headers)).json()
    output["cache_stats"] = stats["points"]
    output["stampede"]["cache_on"]["loads"] = stats["points"]["dashboard.metrics"]["loads"]
    output["stampede"]["cache_on"]["lock_waits"] = stats["points"]["dashboard.metrics"][
        "lock_waits"
    ]
    output["stampede"]["cache_on"]["lock_timeouts"] = stats["points"]["dashboard.metrics"][
        "lock_timeouts"
    ]

    print(
        "[protections] penetration on db_queries="
        f"{output['penetration']['cache_on']['db_queries_total']} "
        f"bloom={output['penetration']['cache_on']['bloom_guard_hits']} | "
        f"off db_queries={output['penetration']['cache_off']['db_queries_total']}"
    )
    print(
        "[protections] negative first="
        f"{output['negative_cache']['first_db_queries']} second="
        f"{output['negative_cache']['second_db_queries']} ttl={output['negative_cache']['ttl_seconds']}"
    )
    print(
        "[protections] stampede on db_queries="
        f"{output['stampede']['cache_on']['db_queries_total']} "
        f"loads={output['stampede']['cache_on']['loads']} "
        f"lock_waits={output['stampede']['cache_on']['lock_waits']} | "
        f"off db_queries={output['stampede']['cache_off']['db_queries_total']}"
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[protections] wrote {output_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--on-url", default="http://127.0.0.1:8011")
    parser.add_argument("--off-url", default="http://127.0.0.1:8012")
    parser.add_argument("--redis-url", default="redis://127.0.0.1:56379/0")
    parser.add_argument("--admin-token", default="dev-admin-token")
    parser.add_argument("--penetration-requests", type=int, default=300)
    parser.add_argument("--penetration-off-requests", type=int, default=60)
    parser.add_argument("--burst-requests", type=int, default=100)
    parser.add_argument("--burst-concurrency", type=int, default=30)
    parser.add_argument("--output", default="evidence/07-protections.json")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
