from __future__ import annotations

import time
from datetime import date

from sqlalchemy import select

from app.cache import get_point
from app.cache.client import cache
from app.demo import CURRENT_PERIOD, ORG_ID, SITE_MAIN
from app.models import CmsBanner, TaxItem


def _find_node(nodes, code):
    for node in nodes:
        if node["code"] == code:
            return node
        found = _find_node(node["children"], code)
        if found:
            return found
    return None


def test_dict_tree_invalidated_after_label_update(client, db):
    assert client.get("/api/tax/dicts/industry/tree").headers["X-Cache-State"] == "miss"
    item = db.execute(select(TaxItem).where(TaxItem.code == "IND-01-01")).scalar_one()
    point = get_point("tax.dict.tree")
    key = point.key(org_id=str(ORG_ID), dict_code="industry")
    assert cache.redis.exists(key) == 1

    new_label = f"更新后的产业 {time.time_ns()}"
    started = time.perf_counter()
    response = client.patch(
        f"/api/admin/tax/items/{item.id}",
        json={"label": new_label},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    write_done = time.perf_counter()
    assert response.status_code == 200
    assert response.headers["X-Invalidation-Keys"] == "1"
    assert cache.redis.exists(key) == 0

    fresh = client.get("/api/tax/dicts/industry/tree")
    read_done = time.perf_counter()
    assert fresh.status_code == 200
    node = _find_node(fresh.json()["tree"], "IND-01-01")
    assert node["label"] == new_label
    observed_ms = (read_done - write_done) * 1000
    assert observed_ms < 250, observed_ms


def test_home_invalidated_after_banner_publish(client, db):
    assert client.get("/api/site/home").status_code == 200
    banner = db.execute(
        select(CmsBanner).where(
            CmsBanner.site_id == SITE_MAIN, CmsBanner.status == "published"
        )
    ).scalars().first()
    assert banner is not None
    point = get_point("site.home")
    key = point.key(
        org_id=str(ORG_ID), site_id=str(SITE_MAIN), date=date.today().isoformat()
    )
    assert cache.redis.exists(key) == 1

    new_title = f"发布后的首页位 {time.time_ns()}"
    response = client.patch(
        f"/api/admin/home/banners/{banner.id}",
        json={"title": new_title, "status": "published"},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    assert cache.redis.exists(key) == 0

    fresh = client.get("/api/site/home")
    titles = [item["title"] for item in fresh.json()["banners"]]
    assert new_title in titles


def test_dashboard_invalidated_after_new_receivable(client, db):
    before = client.get("/api/dashboard/metrics").json()
    assert before["period"] == CURRENT_PERIOD
    point = get_point("dashboard.metrics")
    key = point.key(org_id=str(ORG_ID), site_id=str(SITE_MAIN), period=CURRENT_PERIOD)
    assert cache.redis.exists(key) == 1

    amount = 123456
    response = client.post(
        "/api/admin/dashboard/receivables",
        json={
            "amount_cents": amount,
            "received_cents": 0,
            "due_date": date.today().isoformat(),
            "contract_no": "HT-TEST-00001",
            "period": CURRENT_PERIOD,
        },
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    assert response.headers["X-Invalidation-Keys"] == "1"
    assert cache.redis.exists(key) == 0

    after = client.get("/api/dashboard/metrics").json()
    assert after["receivables"]["total_amount_cents"] == (
        before["receivables"]["total_amount_cents"] + amount
    )
    assert after["receivables"]["plan_count"] == before["receivables"]["plan_count"] + 1


def test_permissions_invalidated_after_role_change(client):
    before = client.get("/api/iam/permissions").json()
    assert "org_admin" in before["roles"]
    point = get_point("iam.perms")
    key = point.key(
        org_id=str(ORG_ID), user_id="33333333-3333-3333-3333-000000000001"
    )
    assert cache.redis.exists(key) == 1

    response = client.patch(
        "/api/admin/iam/users/33333333-3333-3333-3333-000000000001/roles",
        json={"role_codes": ["auditor"]},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    assert response.headers["X-Invalidation-Keys"] == "1"
    assert cache.redis.exists(key) == 0

    after = client.get("/api/iam/permissions").json()
    assert after["roles"] == ["auditor"]
    assert after["permissions"] and after["permission_count"] == len(after["permissions"])

    restore = client.patch(
        "/api/admin/iam/users/33333333-3333-3333-3333-000000000001/roles",
        json={"role_codes": ["org_admin"]},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert restore.status_code == 200


def test_write_returns_commit_and_invalidation_metrics(client, db):
    item = db.execute(select(TaxItem).where(TaxItem.code == "IND-01")).scalar_one()
    response = client.patch(
        f"/api/admin/tax/items/{item.id}",
        json={"label": "指标校验"},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    assert "X-DB-Commit-At" in response.headers
    lag = float(response.headers["X-Invalidation-Ms"])
    assert 0 <= lag < 100
    body = response.json()["invalidated"]
    assert body["reason"] == "tax.item.update"
