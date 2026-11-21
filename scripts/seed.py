#!/usr/bin/env python3
"""初始化演示库：perf（压测数据）或 test（单测数据）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, engine
from app.seed import PROFILES, reset_schema, seed_dataset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=sorted(PROFILES), default="perf")
    parser.add_argument("--reset", action="store_true", help="删除并重建 public schema")
    args = parser.parse_args()

    database = engine.url.database or ""
    if args.reset and database in {"postgres", "template1"}:
        print("拒绝在系统库上执行 --reset", file=sys.stderr)
        return 2
    if args.reset:
        print(f"[seed] reset schema on {database}")
        reset_schema()
    with SessionLocal() as session:
        summary = seed_dataset(session, args.profile)
        session.commit()
    print(f"[seed] profile={summary['profile']} counts={summary['counts']}")
    print(f"[seed] org={summary['org_id']} sites={summary['sites']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
