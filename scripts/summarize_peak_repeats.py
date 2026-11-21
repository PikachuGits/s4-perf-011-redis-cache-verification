#!/usr/bin/env python3
"""汇总 24 并发峰值重复实验的逐轮 JSON，输出可审计的 summary。

输入文件由 scripts/loadtest.py 生成：`*repeat-<n>-cache-on.json` 与
`*repeat-<n>-cache-off.json`。脚本从每轮 `phase.started_at` 推断启动顺序，
并从 `config.seed` 读取 seed；只汇总逐轮统计值，不复制逐请求样本。
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path


def _round_files(input_dir: Path) -> list[tuple[int, Path, Path]]:
    pairs: list[tuple[int, Path, Path]] = []
    for on_path in sorted(input_dir.glob("s4-perf-011-repeat-*-cache-on.json")):
        round_no = int(on_path.name.split("-repeat-")[1].split("-cache-on")[0])
        off_path = input_dir / f"s4-perf-011-repeat-{round_no}-cache-off.json"
        if not off_path.exists():
            raise SystemExit(f"缺少配对文件: {off_path}")
        pairs.append((round_no, on_path, off_path))
    if not pairs:
        raise SystemExit(f"{input_dir} 下没有找到 repeat 文件")
    return pairs


def _stats(summary: dict) -> dict:
    lat = summary["latency_ms"]
    return {
        "count": summary["count"],
        "ok": summary["ok"],
        "p50_ms": lat["p50"],
        "p95_ms": lat["p95"],
        "p99_ms": lat["p99"],
        "max_ms": lat["max"],
        "rps": summary["rps"],
        "db_queries_total": summary["db_queries_total"],
        "db_queries_per_request": summary["db_queries_per_request"],
    }


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default="/tmp")
    parser.add_argument("--output", default="evidence/11-peak-multirun-summary.json")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    runs = []
    for round_no, on_path, off_path in _round_files(input_dir):
        on = json.loads(on_path.read_text(encoding="utf-8"))
        off = json.loads(off_path.read_text(encoding="utf-8"))
        on_start = on["phase"]["started_at"]
        off_start = off["phase"]["started_at"]
        start_order = "cache_on_first" if on_start < off_start else "cache_off_first"
        on_s = _stats(on["summary"])
        off_s = _stats(off["summary"])
        runs.append(
            {
                "round": round_no,
                "start_order": start_order,
                "seed": on["config"]["seed"],
                "cache_on": on_s,
                "cache_off": off_s,
                "paired_p95_delta_pct": round((on_s["p95_ms"] / off_s["p95_ms"] - 1) * 100, 2),
                "paired_rps_delta_pct": round((on_s["rps"] / off_s["rps"] - 1) * 100, 2),
            }
        )

    on_rows = [r["cache_on"] for r in runs]
    off_rows = [r["cache_off"] for r in runs]
    deltas = [r["paired_p95_delta_pct"] for r in runs]

    def by_order(order: str) -> dict:
        rows = [r["paired_p95_delta_pct"] for r in runs if r["start_order"] == order]
        return {"rounds": len(rows), "median": _median(rows)} if rows else {
            "rounds": 0,
            "median": None,
        }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "scripts/summarize_peak_repeats.py 从逐轮 loadtest JSON 汇总；逐轮统计值、seed 与启动顺序已纳入，逐请求样本未纳入",
        "rounds": len(runs),
        "requests_per_round": 4000,
        "concurrency": 24,
        "warmup_requests_per_round": 64,
        "request_seed_varied": len({r["seed"] for r in runs}) == len(runs),
        "seed_range": {"min": min(r["seed"] for r in runs), "max": max(r["seed"] for r in runs)},
        "start_order_counts": {
            "cache_on_first": sum(1 for r in runs if r["start_order"] == "cache_on_first"),
            "cache_off_first": sum(1 for r in runs if r["start_order"] == "cache_off_first"),
        },
        "runs": runs,
        "cache_on": {
            "p95_ms": {"median": _median([r["p95_ms"] for r in on_rows]),
                       "min": min(r["p95_ms"] for r in on_rows),
                       "max": max(r["p95_ms"] for r in on_rows)},
            "p50_ms": {"median": _median([r["p50_ms"] for r in on_rows])},
            "rps": {"median": _median([r["rps"] for r in on_rows])},
            "db_queries_total_per_round": {
                "min": min(r["db_queries_total"] for r in on_rows),
                "max": max(r["db_queries_total"] for r in on_rows),
            },
        },
        "cache_off": {
            "p95_ms": {"median": _median([r["p95_ms"] for r in off_rows]),
                       "min": min(r["p95_ms"] for r in off_rows),
                       "max": max(r["p95_ms"] for r in off_rows)},
            "p50_ms": {"median": _median([r["p50_ms"] for r in off_rows])},
            "rps": {"median": _median([r["rps"] for r in off_rows])},
            "db_queries_total_per_round": {
                "min": min(r["db_queries_total"] for r in off_rows),
                "max": max(r["db_queries_total"] for r in off_rows),
            },
        },
        "paired_p95_delta_pct_cache_on_vs_off": {
            "formula": "(cache_on_p95 / cache_off_p95 - 1) * 100",
            "median": _median(deltas),
            "min": min(deltas),
            "max": max(deltas),
            "median_by_start_order": {
                "cache_on_first": by_order("cache_on_first"),
                "cache_off_first": by_order("cache_off_first"),
            },
        },
        "single_run_context": {
            "repository_report": {
                "source": "evidence/09-peak-cache-on.json and evidence/10-peak-cache-off.json",
                "cache_on_p95_ms": 112.517,
                "cache_off_p95_ms": 103.113,
                "paired_delta_pct": 9.12,
            },
            "independent_retest": {
                "source": "2026-11-21 独立单轮复测",
                "cache_on_p95_ms": 115.464,
                "cache_off_p95_ms": 85.86,
                "paired_delta_pct": 34.48,
            },
        },
        "raw_per_round_data_included": True,
        "raw_request_samples_included": False,
        "limitations": [
            "逐轮统计值、seed 与启动顺序已纳入，可由 runs 数组独立重算中位数、范围与分组。",
            "未纳入逐请求样本；历史单轮逐请求样本见 evidence/09/10。",
            "启动顺序分组差异提示测量结果与顺序相关，8 轮样本不足以判定因果。",
        ],
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
