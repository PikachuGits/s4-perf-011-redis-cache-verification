#!/usr/bin/env python3
"""从缓存点注册表生成 docs/cache-points.md 与 evidence/02-cache-points.json。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cache.registry import BLOOM_KEY_PATTERN, CACHE_POINTS, LOCK_KEY_PATTERN, auxiliary_points
from app.config import get_settings

ROOT = Path(__file__).resolve().parents[1]


def _bounds_text(point) -> str:  # noqa: ANN001
    low, high = point.ttl_bounds(get_settings().cache_ttl_scale)
    return f"{low}–{high} 秒"


def render_markdown() -> str:
    settings = get_settings()
    lines = [
        "# 缓存点清单 · S4-PERF-011",
        "",
        "> 本清单由 `scripts/export_cache_points.py` 从 `app/cache/registry.py` 生成；"
        "文档、管理接口 `/api/admin/cache/points` 与测试共用同一注册表。",
        "",
        f"- Redis：`{settings.redis_url.rsplit('/', 1)[0]}/<db>`，键前缀 `{settings.cache_key_prefix}:`",
        f"- TTL 缩放：`{settings.cache_ttl_scale}`（压测/测试可整体缩放，默认 1.0）",
        "- 所有 UUID 统一小写标准格式；非法键组件在进入缓存前返回 422，不拼接进 Redis 键",
        "",
        "## 一、业务缓存点（4 个）",
        "",
        "| 缓存点 | 键规则 | 基础 TTL | 抖动后范围 | 作用域 | 失效触发 | 缓存内容 | 防护 |",
        "|---|---|---:|---:|---|---|---|---|",
    ]
    for point in CACHE_POINTS.values():
        lines.append(
            "| {name} (`{code}`) | `{pattern}` | {ttl} 秒 | {bounds} | {scope} | {inv} | {payload} | {prot} |".format(
                name=point.name,
                code=point.code,
                pattern=point.pattern,
                ttl=point.ttl_seconds,
                bounds=_bounds_text(point),
                scope=point.scope,
                inv=point.invalidation,
                payload=point.payload,
                prot="；".join(point.protections),
            )
        )

    lines.extend(
        [
            "",
            "## 二、辅助键（穿透防护与并发控制，不是业务内容缓存）",
            "",
            "| 用途 | 键规则 | TTL | 说明 |",
            "|---|---|---:|---|",
        ]
    )
    for point in auxiliary_points():
        lines.append(
            f"| {point.name} | `{point.pattern}` | {point.ttl_seconds} 秒（无抖动） | "
            f"{point.payload}；{point.invalidation} |"
        )
    lines.extend(
        [
            f"| 字典项 Bloom 位图 | `{BLOOM_KEY_PATTERN}` | 3600 秒 | 65536 bit / 5 hash；"
            "字典项新增或删除后由管理接口删除并在下次查询时重建 |",
            f"| single-flight 锁 | `{LOCK_KEY_PATTERN}`（其中 `{{cache_key}}` 为完整业务键） | "
            f"{settings.singleflight_ttl_ms} 毫秒 | `SET NX PX`；Lua 比对 token 后释放，避免误删他人锁 |",
            "",
            "## 三、键空间规则",
            "",
            "1. 键只由 UUID、受控枚举（`dict_code`/`item_code`/`period`/`date`）组成，不包含姓名、手机号、邮箱、token 或用户输入原文。",
            "2. `dict_code` 规则：`^[a-z][a-z0-9_-]{0,63}$`；`item_code` 规则：`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`；`period` 为 `YYYY-MM`；`date` 为服务端本地自然日 `YYYY-MM-DD`。",
            "3. 业务键统一以 `ms:` 开头；管理接口 `POST /api/admin/cache/flush` 只清理该命名空间。",
            "4. 失效在数据库提交之后执行：精确 `DEL` 本请求影响的键；驾驶舱批量改动可按 `ms:dashboard:metrics:{org_id}:{site_id}:*` 前缀清理。",
            "5. 首页按自然日分键，跨日由新键自然替换；权限点变更必须 DEL，不依赖 TTL 到期。",
            "",
            "## 四、TTL 策略说明",
            "",
            "| 缓存点 | 为什么是这个 TTL | 一致性要求 |",
            "|---|---|---|",
            "| 字典树 | 读多写少、全树 JSON 较大（约 300 节点）；15 分钟允许吸收低频改动的回源成本 | 字典项写接口提交后立即 DEL，TTL 只是兜底 |",
            "| 首页聚合 | 运营位/活动/会员卡片允许分钟级延迟；5 分钟兼顾命中率与新鲜度 | 运营发布/修改后按站点 DEL 当日键 |",
            "| 驾驶舱指标 | 聚合扫描多张表，成本最高；10 分钟适合管理驾驶舱的阅读节奏 | 商机/合同/回款写接口按站点+账期 DEL |",
            "| 权限点 | 变更频率最低，30 分钟可显著降低权限校验压力 | 角色或角色权限变更后 DEL 用户键，不等过期 |",
            "",
            "## 五、与验收标准的对应",
            "",
            "- AC-1「缓存点清单写明键规则与过期时间」：本文第一、二、三、四节。",
            "- AC-2「开启与关闭缓存的 P95 与数据库负载对比」：`docs/verification-report.md` 第 3 节 + `evidence/03`、`04`、`05`。",
            "- AC-3「数据修改后读取到新值的时间有实测记录」：`docs/verification-report.md` 第 4 节 + `evidence/06-invalidation.csv`。",
            "",
            "> 数据全部为虚构演示数据，不包含客户名称、真实会员企业或个人信息。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    settings = get_settings()
    docs_dir = ROOT / "docs"
    evidence_dir = ROOT / "evidence"
    docs_dir.mkdir(exist_ok=True)
    evidence_dir.mkdir(exist_ok=True)
    markdown = render_markdown()
    (docs_dir / "cache-points.md").write_text(markdown, encoding="utf-8")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "enabled": settings.cache_enabled,
        "ttl_scale": settings.cache_ttl_scale,
        "points": [point.as_dict(settings.cache_ttl_scale) for point in CACHE_POINTS.values()],
        "auxiliary": [point.as_dict(settings.cache_ttl_scale) for point in auxiliary_points()],
        "bloom_key_pattern": BLOOM_KEY_PATTERN,
        "lock_key_pattern": LOCK_KEY_PATTERN,
        "singleflight_ttl_ms": settings.singleflight_ttl_ms,
    }
    (evidence_dir / "02-cache-points.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {docs_dir / 'cache-points.md'}")
    print(f"wrote {evidence_dir / '02-cache-points.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
