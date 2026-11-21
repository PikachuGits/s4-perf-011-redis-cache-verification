#!/usr/bin/env python3
"""逐缓存点实测「数据修改返回后 → 读到新值」的时延。

每个缓存点独立循环：先读热缓存 → 写接口修改数据（提交后 DEL） → 2ms 间隔轮询读，
直到观察到新值。同时记录服务端 commit→DEL 的耗时（X-Invalidation-Ms）。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import psycopg

from app.demo import CURRENT_PERIOD, ROLE_CODES, SITE_MAIN, USER_IDS


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "p50": round(_percentile(values, 50), 3),
        "p95": round(_percentile(values, 95), 3),
        "max": round(max(values), 3),
        "mean": round(statistics.fmean(values), 3),
    }


def _find_node(nodes: list[dict], code: str) -> dict | None:
    for node in nodes:
        if node["code"] == code:
            return node
        found = _find_node(node.get("children", []), code)
        if found:
            return found
    return None


def _poll(
    client: httpx.Client,
    path: str,
    checker: Callable[[Any], bool],
    timeout_ms: int,
) -> tuple[float, Any]:
    started = time.perf_counter()
    deadline = started + timeout_ms / 1000
    last_body: Any = None
    while time.perf_counter() < deadline:
        response = client.get(path, headers={"X-Site-Id": str(SITE_MAIN)})
        last_body = response.json()
        if response.status_code == 200 and checker(last_body):
            return (time.perf_counter() - started) * 1000, last_body
        time.sleep(0.002)
    return (time.perf_counter() - started) * 1000, last_body


def _connect_dsn(db_url: str) -> str:
    return db_url.replace("postgresql+psycopg://", "postgresql://")


def _lookup_ids(dsn: str) -> dict[str, str]:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ti.id::text
                FROM tax_items ti JOIN tax_dicts td ON td.id = ti.dict_id
                WHERE ti.code = 'IND-01-01' AND ti.deleted_at IS NULL
                LIMIT 1
                """
            )
            tax_item = cur.fetchone()[0]
            cur.execute(
                """
                SELECT id::text FROM cms_banners
                WHERE site_id = %s AND status = 'published' AND deleted_at IS NULL
                ORDER BY sort_order, created_at LIMIT 1
                """,
                (SITE_MAIN,),
            )
            banner = cur.fetchone()[0]
    return {"tax_item_id": tax_item, "banner_id": banner}


def measure_point(
    client: httpx.Client,
    point: str,
    iterations: int,
    read_path: str,
    baseline_getter: Callable[[Any], Any],
    mutate: Callable[[int], httpx.Response],
    checker_factory: Callable[[int, Any], Callable[[Any], bool]],
    timeout_ms: int = 1000,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index in range(iterations):
        baseline_response = client.get(read_path, headers={"X-Site-Id": str(SITE_MAIN)})
        baseline_response.raise_for_status()
        baseline_value = baseline_getter(baseline_response.json())

        write_started = time.perf_counter()
        write_response = mutate(index)
        write_received = time.perf_counter()
        write_response.raise_for_status()
        server_invalidation_ms = float(write_response.headers.get("X-Invalidation-Ms", "0"))
        keys = int(write_response.headers.get("X-Invalidation-Keys", "0"))

        checker = checker_factory(index, baseline_value)
        observed_ms, final_body = _poll(client, read_path, checker, timeout_ms)
        new_value = baseline_getter(final_body) if final_body else None
        records.append(
            {
                "point": point,
                "iteration": index + 1,
                "write_ms": round((write_received - write_started) * 1000, 3),
                "server_commit_to_del_ms": server_invalidation_ms,
                "observed_response_to_new_value_ms": round(observed_ms, 3),
                "invalidated_keys": keys,
                "baseline": baseline_value,
                "observed_new_value": new_value,
                "matched": observed_ms < timeout_ms,
            }
        )
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--db-url", default="postgresql://clawhunt@127.0.0.1:55432/clawhunt_2984")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--out-prefix", default="evidence/06-invalidation")
    parser.add_argument("--admin-token", default="dev-admin-token")
    args = parser.parse_args()

    admin_headers = {"X-Admin-Token": args.admin_token}
    ids = _lookup_ids(_connect_dsn(args.db_url))
    print(f"[invalidation] ids={ids}")

    all_records: list[dict[str, Any]] = []
    with httpx.Client(base_url=args.base_url, timeout=5.0) as client:
        health = client.get("/healthz")
        health.raise_for_status()
        print(f"[invalidation] health={health.json()}")

        # 1) 字典树：改 IND-01-01 自身的 label
        def tax_mutate(index: int) -> httpx.Response:
            return client.patch(
                f"/api/admin/tax/items/{ids['tax_item_id']}",
                json={"label": f"失效实测标签 {index:04d}"},
                headers=admin_headers,
            )

        all_records.extend(
            measure_point(
                client,
                "tax.dict.tree",
                args.iterations,
                "/api/tax/dicts/industry/tree",
                lambda body: (_find_node(body["tree"], "IND-01-01") or {}).get("label"),
                tax_mutate,
                lambda index, baseline: (
                    lambda body: (_find_node(body["tree"], "IND-01-01") or {}).get("label")
                    == f"失效实测标签 {index:04d}"
                ),
            )
        )

        # 2) 首页聚合：改一条已发布 Banner 的标题
        def home_mutate(index: int) -> httpx.Response:
            return client.patch(
                f"/api/admin/home/banners/{ids['banner_id']}",
                json={"title": f"失效实测首页位 {index:04d}", "status": "published"},
                headers=admin_headers,
            )

        all_records.extend(
            measure_point(
                client,
                "site.home",
                args.iterations,
                "/api/site/home",
                lambda body: [item["title"] for item in body["banners"]],
                home_mutate,
                lambda index, baseline: (
                    lambda body: any(
                        item["title"] == f"失效实测首页位 {index:04d}" for item in body["banners"]
                    )
                ),
            )
        )

        # 3) 驾驶舱指标：新增一笔当月应收
        def dashboard_mutate(index: int) -> httpx.Response:
            return client.post(
                "/api/admin/dashboard/receivables",
                json={
                    "amount_cents": 100000 + index,
                    "received_cents": 0,
                    "due_date": date.today().isoformat(),
                    "contract_no": f"HT-INV-{index:04d}",
                    "period": CURRENT_PERIOD,
                },
                headers=admin_headers,
            )

        all_records.extend(
            measure_point(
                client,
                "dashboard.metrics",
                args.iterations,
                f"/api/dashboard/metrics?period={CURRENT_PERIOD}",
                lambda body: body["receivables"]["total_amount_cents"],
                dashboard_mutate,
                lambda index, baseline: (
                    lambda body: body["receivables"]["total_amount_cents"]
                    >= baseline + 100000 + index
                ),
            )
        )

        # 4) 权限点：把用户切到一个当前不包含的角色
        role_state = client.get(
            "/api/iam/permissions", headers={"X-User-Id": str(USER_IDS[0])}
        ).json()["roles"]

        def perms_mutate(index: int) -> httpx.Response:
            nonlocal role_state
            target = next(role for role in ROLE_CODES if role not in role_state)
            response = client.patch(
                f"/api/admin/iam/users/{USER_IDS[0]}/roles",
                json={"role_codes": [target]},
                headers=admin_headers,
            )
            if response.status_code == 200:
                role_state = [target]
            return response

        all_records.extend(
            measure_point(
                client,
                "iam.perms",
                args.iterations,
                "/api/iam/permissions",
                lambda body: body["roles"],
                perms_mutate,
                lambda index, baseline: (lambda body: body["roles"] != baseline),
            )
        )

        client.patch(
            f"/api/admin/iam/users/{USER_IDS[0]}/roles",
            json={"role_codes": ["org_admin"]},
            headers=admin_headers,
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in all_records:
        grouped.setdefault(record["point"], []).append(record)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "iterations_per_point": args.iterations,
        "points": {
            point: {
                "observed_response_to_new_value_ms": _stats(
                    [record["observed_response_to_new_value_ms"] for record in records]
                ),
                "server_commit_to_del_ms": _stats(
                    [record["server_commit_to_del_ms"] for record in records]
                ),
                "all_matched": all(record["matched"] for record in records),
            }
            for point, records in grouped.items()
        },
        "records": all_records,
    }

    prefix = Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with prefix.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_records[0]))
        writer.writeheader()
        writer.writerows(all_records)

    for point, records in grouped.items():
        observed = summary["points"][point]["observed_response_to_new_value_ms"]
        server = summary["points"][point]["server_commit_to_del_ms"]
        print(
            f"[invalidation] {point}: observed p50={observed['p50']}ms p95={observed['p95']}ms "
            f"max={observed['max']}ms | server p50={server['p50']}ms p95={server['p95']}ms "
            f"matched={summary['points'][point]['all_matched']}"
        )
    print(f"[invalidation] wrote {prefix.with_suffix('.json')} and .csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
