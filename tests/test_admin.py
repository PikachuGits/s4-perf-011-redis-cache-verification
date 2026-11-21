from __future__ import annotations

from datetime import date

from app.cache.registry import CACHE_POINTS, auxiliary_points


def test_admin_endpoints_require_token(client):
    assert client.get("/api/admin/cache/points").status_code == 401
    assert (
        client.get("/api/admin/cache/points", headers={"X-Admin-Token": "wrong"}).status_code
        == 401
    )
    response = client.get(
        "/api/admin/cache/points", headers={"X-Admin-Token": "test-admin-token"}
    )
    assert response.status_code == 200


def test_cache_points_endpoint_matches_registry(client, admin_headers):
    body = client.get("/api/admin/cache/points", headers=admin_headers).json()
    assert body["enabled"] is True
    assert {item["code"] for item in body["points"]} == set(CACHE_POINTS)
    assert len(body["auxiliary"]) == len(auxiliary_points())
    for item in body["points"]:
        assert item["pattern"].startswith("ms:")
        assert item["ttl_seconds"] > 0
        assert item["ttl_bounds_seconds"][0] <= item["ttl_seconds"]
        assert item["protections"]


def test_cache_stats_shape(client, admin_headers):
    client.get("/api/tax/dicts/industry/tree")
    client.get("/api/tax/dicts/industry/tree")
    body = client.get("/api/admin/cache/stats", headers=admin_headers).json()
    assert body["enabled"] is True
    assert body["totals"]["reads"] >= 2
    assert body["totals"]["hits"] >= 1
    tree = body["points"]["tax.dict.tree"]
    assert tree["hits"] >= 1
    assert tree["misses"] >= 1
    assert "db" in body


def test_admin_context_is_demo_only(client, admin_headers):
    body = client.get("/api/admin/context", headers=admin_headers).json()
    assert body["org_id"] == "11111111-1111-1111-1111-111111111111"
    assert len(body["users"]) == 40
    assert body["periods"]


def test_admin_rejects_invalid_receivable(client, admin_headers):
    response = client.post(
        "/api/admin/dashboard/receivables",
        json={
            "amount_cents": 100,
            "received_cents": 101,
            "due_date": date.today().isoformat(),
            "contract_no": "HT-BAD-1",
        },
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_admin_rejects_unknown_role(client, admin_headers):
    response = client.patch(
        "/api/admin/iam/users/33333333-3333-3333-3333-000000000001/roles",
        json={"role_codes": ["not_a_role"]},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_cache_flush_removes_namespace(client, admin_headers):
    client.get("/api/tax/dicts/industry/tree")
    assert any(key.startswith("ms:") for key in _scan_keys(client))
    body = client.post("/api/admin/cache/flush", headers=admin_headers).json()
    assert body["ok"] is True
    assert body["deleted"] >= 1
    assert not any(key.startswith("ms:") for key in _scan_keys(client))


def _scan_keys(client):
    from app.cache import cache

    return list(cache.redis.scan_iter(match="ms:*"))


def test_health_and_root(client):
    health = client.get("/healthz").json()
    assert health["ok"] is True
    assert health["db"] is True
    assert health["redis"] is True
    assert client.get("/").json()["task"] == "S4-PERF-011"
