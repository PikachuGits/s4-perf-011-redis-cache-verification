from __future__ import annotations

import threading
import time

from redis.exceptions import RedisError

from app.cache import cache, get_point
from app.cache.client import CacheRequestState
from app.demo import CURRENT_PERIOD, ORG_ID, SITE_MAIN


def test_negative_cache_for_missing_dict(client):
    path = "/api/tax/dicts/does_not_exist/tree"
    first = client.get(path)
    assert first.status_code == 404
    assert int(first.headers["X-DB-Queries"]) == 1

    second = client.get(path)
    assert second.status_code == 404
    assert second.headers["X-Cache-State"] == "negative"
    assert int(second.headers["X-DB-Queries"]) == 0

    point = get_point("tax.dict.tree")
    key = point.key(org_id=str(ORG_ID), dict_code="does_not_exist")
    ttl = cache.redis.ttl(key)
    assert 0 < ttl <= 35


def test_bloom_rejects_unknown_dictionary_items_without_database(client, admin_headers):
    warm = client.get("/api/tax/dicts/industry/items/IND-01-01")
    assert warm.status_code == 200
    assert client.post("/api/admin/cache/reset-stats", headers=admin_headers).status_code == 200

    for index in range(60):
        response = client.get(f"/api/tax/dicts/industry/items/NOT-EXIST-{index:04d}")
        assert response.status_code == 404
        assert response.json()["detail"]["guard"] == "bloom"
        assert int(response.headers["X-DB-Queries"]) == 0

    stats = client.get("/api/admin/cache/stats", headers=admin_headers).json()
    assert stats["points"]["tax.dict.item.negative"]["bloom_rejects"] >= 60
    assert stats["db"]["db_queries"] == 0


def test_single_flight_prevents_stampede():
    point = get_point("dashboard.metrics")
    params = {"org_id": str(ORG_ID), "site_id": str(SITE_MAIN), "period": CURRENT_PERIOD}
    cache.redis.delete(point.key(**params))
    cache.reset_stats()

    calls = 0
    calls_lock = threading.Lock()

    def loader():
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.08)
        return {"ok": True, "marker": "single-flight"}

    barrier = threading.Barrier(20)
    results: list[dict] = []

    def worker():
        barrier.wait()
        results.append(cache.get_or_load(point, params, loader, request_state=CacheRequestState()))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 20
    assert all(result == results[0] for result in results)
    assert calls == 1
    stats = cache.stats_snapshot()["points"]["dashboard.metrics"]
    assert stats["loads"] == 1
    assert stats["lock_waits"] == 19


def test_redis_failure_falls_back_to_database(client):
    original_redis = cache._redis

    class BrokenRedis:
        def get(self, *args, **kwargs):
            raise RedisError("simulated outage")

        def set(self, *args, **kwargs):
            raise RedisError("simulated outage")

    cache._redis = BrokenRedis()
    try:
        response = client.get("/api/tax/dicts/industry/tree")
    finally:
        cache._redis = original_redis

    assert response.status_code == 200
    assert response.headers["X-Cache-State"] == "error"
    assert int(response.headers["X-DB-Queries"]) == 2
    stats = cache.stats_snapshot()["points"]["tax.dict.tree"]
    assert stats["errors"] >= 1


def test_cache_disabled_always_hits_database(client):
    cache.enabled = False
    try:
        first = client.get("/api/site/home")
        second = client.get("/api/site/home")
    finally:
        cache.enabled = True

    for response in (first, second):
        assert response.status_code == 200
        assert response.headers["X-Cache-Enabled"] == "0"
        assert response.headers["X-Cache-State"] == "bypass"
        assert int(response.headers["X-DB-Queries"]) == 4
    stats = cache.stats_snapshot()["points"]["site.home"]
    assert stats["bypass"] == 2
    assert stats["hits"] == 0
