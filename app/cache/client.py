"""Cache-aside 客户端：命中/回源、single-flight、负缓存、TTL 抖动与失效统计。"""
from __future__ import annotations

import contextvars
import json
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, TypeVar

from redis import Redis
from redis.exceptions import RedisError

from app.cache.bloom import RedisBloom
from app.cache.registry import BLOOM_KEY_PATTERN, LOCK_KEY_PATTERN, CachePoint, validate_component
from app.config import Settings, get_settings

T = TypeVar("T")

NULL_SENTINEL = "__MSP_NULL__"

UNLOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


@dataclass
class CacheRequestState:
    point: str | None = None
    state: str | None = None
    key: str | None = None

    def set(self, point: CachePoint, state: str, key: str) -> None:
        self.point = point.code
        self.state = state
        self.key = key


_state: contextvars.ContextVar[CacheRequestState | None] = contextvars.ContextVar(
    "cache_request_state", default=None
)


def new_request_state() -> CacheRequestState:
    state = CacheRequestState()
    _state.set(state)
    return state


def current_cache_state() -> CacheRequestState | None:
    return _state.get()


@dataclass
class PointStats:
    hits: int = 0
    misses: int = 0
    negative_hits: int = 0
    bypass: int = 0
    errors: int = 0
    loads: int = 0
    bloom_rejects: int = 0
    lock_waits: int = 0
    lock_timeouts: int = 0
    load_time_ms: float = 0.0
    redis_time_ms: float = 0.0
    lock_wait_ms: float = 0.0


class CacheClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.enabled = self.settings.cache_enabled
        self._lock = threading.Lock()
        self._redis = Redis.from_url(
            self.settings.redis_url,
            decode_responses=True,
            socket_timeout=self.settings.redis_socket_timeout,
            socket_connect_timeout=self.settings.redis_socket_timeout,
            health_check_interval=30,
        )
        self._stats: dict[str, PointStats] = {}
        self._last_invalidation: dict[str, Any] | None = None
        self.reset_stats()

    # ------------------------------------------------------------------ stats
    @property
    def redis(self) -> Redis:
        return self._redis

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = {}
            self._last_invalidation = None

    def _get_stats(self, code: str) -> PointStats:
        with self._lock:
            return self._stats.setdefault(code, PointStats())

    def _add(self, code: str, field: str, value: float | int = 1) -> None:
        with self._lock:
            stats = self._stats.setdefault(code, PointStats())
            setattr(stats, field, getattr(stats, field) + value)

    def stats_snapshot(self) -> dict[str, Any]:
        with self._lock:
            points = {code: asdict(stats) for code, stats in self._stats.items()}
            last_invalidation = dict(self._last_invalidation) if self._last_invalidation else None
        totals = {
            "hits": sum(p["hits"] for p in points.values()),
            "misses": sum(p["misses"] for p in points.values()),
            "negative_hits": sum(p["negative_hits"] for p in points.values()),
            "bypass": sum(p["bypass"] for p in points.values()),
            "errors": sum(p["errors"] for p in points.values()),
            "loads": sum(p["loads"] for p in points.values()),
            "bloom_rejects": sum(p["bloom_rejects"] for p in points.values()),
            "lock_waits": sum(p["lock_waits"] for p in points.values()),
            "lock_timeouts": sum(p["lock_timeouts"] for p in points.values()),
            "load_time_ms": round(sum(p["load_time_ms"] for p in points.values()), 3),
            "redis_time_ms": round(sum(p["redis_time_ms"] for p in points.values()), 3),
        }
        reads = totals["hits"] + totals["misses"] + totals["negative_hits"] + totals["bypass"]
        totals["reads"] = reads
        totals["hit_rate"] = round(totals["hits"] / reads, 4) if reads else 0.0
        return {
            "enabled": self.enabled,
            "key_prefix": self.settings.cache_key_prefix,
            "redis_url_db": self.settings.redis_url.rsplit("/", 1)[-1],
            "points": points,
            "totals": totals,
            "last_invalidation": last_invalidation,
        }

    # ------------------------------------------------------------------ redis
    def ping(self) -> bool:
        try:
            return bool(self._redis.ping())
        except RedisError:
            return False

    def _redis_call(self, point: CachePoint, func: Callable[[], T], default: T) -> T:
        started = time.perf_counter_ns()
        try:
            return func()
        except RedisError:
            self._add(point.code, "errors")
            return default
        finally:
            self._add(point.code, "redis_time_ms", (time.perf_counter_ns() - started) / 1_000_000)

    def _load(self, point: CachePoint, loader: Callable[[], T]) -> T:
        started = time.perf_counter_ns()
        try:
            return loader()
        finally:
            self._add(point.code, "loads")
            self._add(point.code, "load_time_ms", (time.perf_counter_ns() - started) / 1_000_000)

    @staticmethod
    def _encode(value: Any) -> str:
        if value is None:
            return NULL_SENTINEL
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def _decode(raw: str) -> Any:
        if raw == NULL_SENTINEL:
            return None
        return json.loads(raw)

    def _ttl(self, point: CachePoint) -> int:
        return point.effective_ttl(self.settings.cache_ttl_scale)

    def _lock_key(self, cache_key: str) -> str:
        return LOCK_KEY_PATTERN.format(cache_key=cache_key)

    def _set_cache(
        self,
        point: CachePoint,
        key: str,
        value: Any,
        request_state: CacheRequestState | None,
        ttl_seconds: int | None = None,
    ) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl(point)
        payload = self._encode(value)
        started = time.perf_counter_ns()
        try:
            self._redis.set(key, payload, ex=ttl)
            self._add(point.code, "redis_time_ms", (time.perf_counter_ns() - started) / 1_000_000)
        except RedisError:
            self._add(point.code, "errors")

    def _get_raw(self, point: CachePoint, key: str) -> tuple[bool, str | None]:
        started = time.perf_counter_ns()
        try:
            return True, self._redis.get(key)
        except RedisError:
            self._add(point.code, "errors")
            return False, None
        finally:
            self._add(point.code, "redis_time_ms", (time.perf_counter_ns() - started) / 1_000_000)

    def _try_lock(self, point: CachePoint, lock_key: str, token: str) -> tuple[bool, bool]:
        started = time.perf_counter_ns()
        try:
            acquired = bool(
                self._redis.set(lock_key, token, nx=True, px=self.settings.singleflight_ttl_ms)
            )
            return True, acquired
        except RedisError:
            self._add(point.code, "errors")
            return False, False
        finally:
            self._add(point.code, "redis_time_ms", (time.perf_counter_ns() - started) / 1_000_000)

    def _unlock(self, point: CachePoint, lock_key: str, token: str) -> None:
        self._redis_call(point, lambda: self._redis.eval(UNLOCK_SCRIPT, 1, lock_key, token), 0)

    def get_or_load(
        self,
        point: CachePoint,
        params: dict[str, str],
        loader: Callable[[], T],
        *,
        allow_negative: bool = True,
        negative_ttl_seconds: int = 30,
        request_state: CacheRequestState | None = None,
    ) -> T:
        """读缓存，未命中则 single-flight 回源并写回。Redis 异常时降级为直接回源。"""
        state = request_state or current_cache_state()
        key = point.key(**params)
        if state:
            state.point = point.code
            state.key = key

        if not self.enabled:
            self._add(point.code, "bypass")
            if state:
                state.state = "bypass"
            return self._load(point, loader)

        ok, raw = self._get_raw(point, key)
        if not ok:
            if state:
                state.state = "error"
            return self._load(point, loader)

        if raw is not None:
            if raw == NULL_SENTINEL:
                self._add(point.code, "negative_hits")
                if state:
                    state.state = "negative"
                return None
            self._add(point.code, "hits")
            if state:
                state.state = "hit"
            return self._decode(raw)

        self._add(point.code, "misses")
        token = uuid.uuid4().hex
        lock_key = self._lock_key(key)
        lock_ok, acquired = self._try_lock(point, lock_key, token)
        if not lock_ok:
            if state:
                state.state = "error"
            return self._load(point, loader)

        if acquired:
            try:
                reloaded_ok, raw = self._get_raw(point, key)
                if reloaded_ok and raw is not None:
                    if raw == NULL_SENTINEL:
                        self._add(point.code, "negative_hits")
                        if state:
                            state.state = "negative"
                        return None
                    self._add(point.code, "hits")
                    if state:
                        state.state = "hit_after_wait"
                    return self._decode(raw)

                value = self._load(point, loader)
                if value is None and allow_negative:
                    self._set_cache(
                        point, key, None, state, ttl_seconds=max(1, negative_ttl_seconds)
                    )
                elif value is not None:
                    self._set_cache(point, key, value, state)
                if state:
                    state.state = "miss"
                return value
            finally:
                self._unlock(point, lock_key, token)

        # 未抢到锁：短暂轮询等待持锁者写回缓存，超时则回源兜底
        self._add(point.code, "lock_waits")
        if state:
            state.state = "wait"
        started = time.perf_counter_ns()
        deadline = (
            time.monotonic()
            + self.settings.singleflight_wait_ms / 1000 * random.uniform(0.75, 1.0)
        )
        while time.monotonic() < deadline:
            time.sleep(0.01)
            wait_ok, raw = self._get_raw(point, key)
            if not wait_ok:
                break
            if raw is not None:
                self._add(point.code, "lock_wait_ms", (time.perf_counter_ns() - started) / 1_000_000)
                if raw == NULL_SENTINEL:
                    self._add(point.code, "negative_hits")
                    if state:
                        state.state = "negative"
                    return None
                self._add(point.code, "hits")
                if state:
                    state.state = "hit_after_wait"
                return self._decode(raw)

        self._add(point.code, "lock_timeouts")
        self._add(point.code, "lock_wait_ms", (time.perf_counter_ns() - started) / 1_000_000)
        value = self._load(point, loader)
        if state:
            state.state = "lock_timeout"
        return value

    # ------------------------------------------------------------ invalidation
    def invalidate_keys(
        self,
        keys: list[str],
        *,
        commit_perf_ns: int | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        if not self.enabled or not keys:
            return {"keys": 0, "lag_ms": 0.0, "reason": reason}
        started = time.perf_counter_ns()
        try:
            deleted = int(self._redis.delete(*keys))
        except RedisError:
            deleted = 0
        finished = time.perf_counter_ns()
        lag_ms = (finished - commit_perf_ns) / 1_000_000 if commit_perf_ns else 0.0
        record = {
            "keys": deleted,
            "requested_keys": len(keys),
            "lag_ms": round(lag_ms, 3),
            "reason": reason,
            "at": time.time(),
        }
        with self._lock:
            self._last_invalidation = record
        return record

    def invalidate_pattern(
        self,
        pattern: str,
        *,
        commit_perf_ns: int | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"keys": 0, "lag_ms": 0.0, "reason": reason}
        started = time.perf_counter_ns()
        keys: list[str] = []
        try:
            for key in self._redis.scan_iter(match=pattern, count=200):
                keys.append(key)
            if keys:
                self._redis.delete(*keys)
        except RedisError:
            pass
        finished = time.perf_counter_ns()
        lag_ms = (finished - commit_perf_ns) / 1_000_000 if commit_perf_ns else 0.0
        record = {
            "keys": len(keys),
            "requested_keys": len(keys),
            "lag_ms": round(lag_ms, 3),
            "reason": reason,
            "at": time.time(),
        }
        with self._lock:
            self._last_invalidation = record
        return record

    def last_invalidation(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._last_invalidation) if self._last_invalidation else None

    def flush_namespace(self) -> int:
        if not self.enabled:
            return 0
        deleted = 0
        for key in self._redis.scan_iter(match="ms:*", count=500):
            deleted += int(self._redis.delete(key))
        return deleted

    # ----------------------------------------------------------- negative/bloom
    def note_bloom_reject(self, point: CachePoint, state: CacheRequestState | None = None) -> None:
        self._add(point.code, "bloom_rejects")
        state = state or current_cache_state()
        if state:
            state.point = point.code
            state.state = "bloom_reject"

    def get_negative(
        self,
        point: CachePoint,
        params: dict[str, str],
        request_state: CacheRequestState | None = None,
    ) -> bool:
        """返回 True 表示命中「不存在」负缓存。"""
        state = request_state or current_cache_state()
        key = point.key(**params)
        if state:
            state.point = point.code
            state.key = key
        if not self.enabled:
            self._add(point.code, "bypass")
            return False
        raw = self._redis_call(point, lambda: self._redis.get(key), None)
        if raw == NULL_SENTINEL:
            self._add(point.code, "negative_hits")
            if state:
                state.state = "negative"
            return True
        return False

    def set_negative(
        self,
        point: CachePoint,
        params: dict[str, str],
        request_state: CacheRequestState | None = None,
    ) -> None:
        state = request_state or current_cache_state()
        key = point.key(**params)
        if state:
            state.point = point.code
            state.key = key
        if not self.enabled:
            return
        self._set_cache(point, key, None, state)
        if state:
            state.state = "negative_store"

    def bloom_for(self, org_id: str, dict_code: str, ttl_seconds: int = 3600) -> RedisBloom:
        key = BLOOM_KEY_PATTERN.format(
            org_id=validate_component("org_id", org_id),
            dict_code=validate_component("dict_code", dict_code),
        )
        return RedisBloom(self._redis, key, ttl_seconds=ttl_seconds)


cache = CacheClient()
