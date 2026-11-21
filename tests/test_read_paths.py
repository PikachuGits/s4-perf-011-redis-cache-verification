from __future__ import annotations

import re
from datetime import date

import pytest

from app.cache import get_point
from app.cache.client import cache
from app.demo import CURRENT_PERIOD

CASES = [
    ("/api/tax/dicts/industry/tree", "tax.dict.tree", 2),
    ("/api/site/home", "site.home", 4),
    ("/api/dashboard/metrics", "dashboard.metrics", 3),
    ("/api/iam/permissions", "iam.perms", 3),
]


@pytest.mark.parametrize("path,point_code,expected_queries", CASES)
def test_cache_hit_skips_database(client, path, point_code, expected_queries):
    first = client.get(path)
    assert first.status_code == 200
    assert first.headers["X-Cache-State"] == "miss"
    assert int(first.headers["X-DB-Queries"]) == expected_queries
    assert first.headers["X-Cache-Point"] == point_code

    second = client.get(path)
    assert second.status_code == 200
    assert second.headers["X-Cache-State"] == "hit"
    assert int(second.headers["X-DB-Queries"]) == 0
    assert second.json() == first.json()


@pytest.mark.parametrize("path,point_code,expected_queries", CASES)
def test_cache_key_ttl_within_bounds(client, path, point_code, expected_queries):
    response = client.get(path)
    assert response.status_code == 200
    point = get_point(point_code)
    date_value = date.today().isoformat()
    period = CURRENT_PERIOD
    params = {
        "org_id": "11111111-1111-1111-1111-111111111111",
        "site_id": "22222222-2222-2222-2222-222222222222",
        "user_id": "33333333-3333-3333-3333-000000000001",
        "dict_code": "industry",
        "date": date_value,
        "period": period,
    }
    placeholders = set(re.findall(r"{(\w+)}", point.pattern))
    key = point.key(**{name: params[name] for name in placeholders})
    ttl = cache.redis.ttl(key)
    low, high = point.ttl_bounds()
    assert low <= ttl <= high, (ttl, low, high)


def test_permissions_and_tree_payload_shapes(client):
    tree = client.get("/api/tax/dicts/industry/tree").json()
    assert tree["dict_code"] == "industry"
    assert tree["tree"] and tree["tree"][0]["children"]

    perms = client.get("/api/iam/permissions").json()
    assert perms["roles"]
    assert perms["permission_count"] == len(perms["permissions"])
    assert len(perms["permissions"]) == len(set(perms["permissions"]))


def test_home_and_dashboard_payload_shapes(client):
    home = client.get("/api/site/home").json()
    assert home["banners"]
    assert home["upcoming_events"]
    assert home["counts"]["members"] >= 1

    dashboard = client.get("/api/dashboard/metrics").json()
    assert dashboard["period"]
    assert dashboard["opportunities"]["total_count"] >= 1
    assert dashboard["receivables"]["collection_rate"] >= 0
