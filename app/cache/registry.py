"""缓存点注册表：键规则、TTL、失效触发与防护措施的唯一来源。

文档、管理接口和测试都从该注册表读取，避免「文档写一套、代码跑一套」。
"""
from __future__ import annotations

import random
import re
import uuid
from dataclasses import asdict, dataclass, field

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ITEM_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_component(name: str, value: str) -> str:
    value = str(value)
    if name.endswith("_id"):
        try:
            return str(uuid.UUID(value))
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"invalid uuid for {name}") from exc
    if name == "dict_code":
        if not CODE_RE.match(value):
            raise ValueError("invalid dict_code")
    elif name == "item_code":
        if not ITEM_CODE_RE.match(value):
            raise ValueError("invalid item_code")
    elif name == "period":
        if not PERIOD_RE.match(value):
            raise ValueError("invalid period")
    elif name == "date":
        if not DATE_RE.match(value):
            raise ValueError("invalid date")
    else:
        raise ValueError(f"unsupported cache key component: {name}")
    return value


@dataclass(frozen=True)
class CachePoint:
    code: str
    name: str
    pattern: str
    ttl_seconds: int
    scope: str
    invalidation: str
    payload: str
    jitter_ratio: float = 0.10
    protections: tuple[str, ...] = field(default_factory=tuple)

    def key(self, **params: str) -> str:
        placeholders = set(re.findall(r"{(\w+)}", self.pattern))
        missing = placeholders - set(params)
        extra = set(params) - placeholders
        if missing or extra:
            raise ValueError(f"cache key params mismatch: missing={missing} extra={extra}")
        values = {k: validate_component(k, v) for k, v in params.items()}
        try:
            return self.pattern.format(**values)
        except (KeyError, IndexError) as exc:
            raise ValueError("invalid cache key pattern") from exc

    def effective_ttl(self, scale: float = 1.0) -> int:
        base = max(1, int(round(self.ttl_seconds * scale)))
        if self.jitter_ratio <= 0:
            return base
        spread = base * self.jitter_ratio
        return max(1, int(round(base + random.uniform(-spread, spread))))

    def ttl_bounds(self, scale: float = 1.0) -> tuple[int, int]:
        base = max(1, int(round(self.ttl_seconds * scale)))
        spread = base * self.jitter_ratio
        return max(1, int(round(base - spread))), max(1, int(round(base + spread)))

    def as_dict(self, scale: float = 1.0) -> dict:
        data = asdict(self)
        data["protections"] = list(self.protections)
        data["ttl_bounds_seconds"] = list(self.ttl_bounds(scale))
        return data


CACHE_POINTS: dict[str, CachePoint] = {
    "tax.dict.tree": CachePoint(
        code="tax.dict.tree",
        name="字典树（S2-TAX 字典与标签中心）",
        pattern="ms:tax:dict:tree:{org_id}:{dict_code}",
        ttl_seconds=900,
        scope="org_id + dict_code",
        invalidation="字典项新增/改名/排序/软删除：数据库提交后 DEL 该字典的树键；下一次读取重建",
        payload="整棵字典树 JSON（industry/tags/region，约 300 个节点）",
        jitter_ratio=0.10,
        protections=("single-flight 分布式锁", "TTL ±10% 抖动", "Redis 故障时回源 DB"),
    ),
    "site.home": CachePoint(
        code="site.home",
        name="首页聚合",
        pattern="ms:site:home:{org_id}:{site_id}:{date}",
        ttl_seconds=300,
        scope="org_id + site_id + 自然日",
        invalidation="Banner 发布/修改、活动或会员资料变更：提交后按站点 DEL 当日键",
        payload="Banner 5 条 + 近期活动 5 条 + 推荐会员 5 条 + 三项计数",
        jitter_ratio=0.10,
        protections=("single-flight 分布式锁", "TTL ±10% 抖动", "按自然日分键，跨日自然失效"),
    ),
    "dashboard.metrics": CachePoint(
        code="dashboard.metrics",
        name="驾驶舱指标",
        pattern="ms:dashboard:metrics:{org_id}:{site_id}:{period}",
        ttl_seconds=600,
        scope="org_id + site_id + 账期（YYYY-MM）",
        invalidation="商机阶段、合同状态、回款计划变更：提交后按站点+账期 DEL（批量改动按前缀清理）",
        payload="商机阶段汇总 + 合同状态汇总 + 回款应收/实收/逾期汇总",
        jitter_ratio=0.10,
        protections=("single-flight 分布式锁", "TTL ±10% 抖动", "按账期分键，避免跨月脏读"),
    ),
    "iam.perms": CachePoint(
        code="iam.perms",
        name="权限点",
        pattern="ms:iam:perms:{org_id}:{user_id}",
        ttl_seconds=1800,
        scope="org_id + user_id",
        invalidation="用户角色或角色权限变更：提交后 DEL 该用户键；支持传入多个用户批量清理",
        payload="用户角色列表 + 去重后的 allow 权限点列表",
        jitter_ratio=0.05,
        protections=("single-flight 分布式锁", "TTL ±5% 抖动", "权限变更立即 DEL，不依赖过期"),
    ),
}

AUXILIARY_POINTS: dict[str, CachePoint] = {
    "tax.dict.item.negative": CachePoint(
        code="tax.dict.item.negative",
        name="字典项查询负缓存（穿透防护）",
        pattern="ms:tax:dict:item:neg:{org_id}:{dict_code}:{item_code}",
        ttl_seconds=30,
        scope="org_id + dict_code + item_code",
        invalidation="仅缓存「不存在」结果，30 秒自然过期；字典项新增后重建 Bloom 并删除负缓存",
        payload="固定空值哨兵（不缓存真实内容）",
        jitter_ratio=0.0,
        protections=("Bloom 过滤器先判不存在", "空值哨兵负缓存", "短 TTL 限制脏窗口"),
    ),
}

BLOOM_KEY_PATTERN = "ms:tax:bloom:{org_id}:{dict_code}"
LOCK_KEY_PATTERN = "ms:lock:{cache_key}"


def get_point(code: str) -> CachePoint:
    return CACHE_POINTS[code]


def all_points() -> list[CachePoint]:
    return list(CACHE_POINTS.values())


def auxiliary_points() -> list[CachePoint]:
    return list(AUXILIARY_POINTS.values())
