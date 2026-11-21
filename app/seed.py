from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.demo import (
    DICT_CODES,
    ORG_ID,
    PERMISSION_CODES,
    ROLE_CODES,
    SITE_B,
    SITE_C,
    SITE_MAIN,
    SITES,
    USER_IDS,
    user_id,
)
from app.models import (
    Base,
    CmsBanner,
    IamRole,
    IamRolePermission,
    IamUser,
    IamUserRole,
    MemberCompany,
    ResourceListing,
    SiteEvent,
    TaxDict,
    TaxItem,
)

PROFILES: dict[str, dict[str, int]] = {
    "perf": {
        "tax_l1": 10,
        "tax_l2": 10,
        "tags": 150,
        "banners_main": 24,
        "banners_other": 12,
        "events_main": 300,
        "events_other": 80,
        "members_main": 120,
        "members_other": 60,
        "resources_main": 500,
        "resources_other": 200,
        "users": 40,
        "opps_main": 20000,
        "opps_other": 2000,
        "contracts_main": 8000,
        "contracts_other": 1000,
        "receivables_main": 16000,
        "receivables_other": 2000,
    },
    "test": {
        "tax_l1": 3,
        "tax_l2": 2,
        "tags": 8,
        "banners_main": 5,
        "banners_other": 2,
        "events_main": 12,
        "events_other": 4,
        "members_main": 15,
        "members_other": 6,
        "resources_main": 30,
        "resources_other": 10,
        "users": 8,
        "opps_main": 200,
        "opps_other": 30,
        "contracts_main": 100,
        "contracts_other": 20,
        "receivables_main": 200,
        "receivables_other": 30,
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seed_tax(session: Session, counts: dict[str, int]) -> None:
    for dict_code, dict_name in (
        ("industry", "产业方向"),
        ("tags", "资源标签"),
        ("region", "地域"),
    ):
        tax_dict = TaxDict(
            org_id=ORG_ID,
            site_id=SITE_MAIN,
            code=dict_code,
            name=dict_name,
            description=f"虚构字典 {dict_code}",
            created_by=user_id(1),
        )
        session.add(tax_dict)
        session.flush()
        if dict_code == "industry":
            root = TaxItem(
                org_id=ORG_ID, site_id=SITE_MAIN, dict_id=tax_dict.id,
                parent_id=None, code="IND", label="全部产业", level=1, sort_order=0,
                created_by=user_id(1),
            )
            session.add(root)
            session.flush()
            for i in range(1, counts["tax_l1"] + 1):
                l1 = TaxItem(
                    org_id=ORG_ID, site_id=SITE_MAIN, dict_id=tax_dict.id,
                    parent_id=root.id, code=f"IND-{i:02d}", label=f"一级产业 {i:02d}",
                    level=2, sort_order=i, created_by=user_id(1),
                )
                session.add(l1)
                session.flush()
                for j in range(1, counts["tax_l2"] + 1):
                    session.add(
                        TaxItem(
                            org_id=ORG_ID, site_id=SITE_MAIN, dict_id=tax_dict.id,
                            parent_id=l1.id, code=f"IND-{i:02d}-{j:02d}",
                            label=f"二级产业 {i:02d}-{j:02d}", level=3, sort_order=j,
                            created_by=user_id(1),
                        )
                    )
        elif dict_code == "tags":
            for i in range(1, counts["tags"] + 1):
                session.add(
                    TaxItem(
                        org_id=ORG_ID, site_id=SITE_MAIN, dict_id=tax_dict.id,
                        parent_id=None, code=f"TAG-{i:03d}", label=f"标签 {i:03d}",
                        level=1, sort_order=i, created_by=user_id(1),
                    )
                )
        else:
            root = TaxItem(
                org_id=ORG_ID, site_id=SITE_MAIN, dict_id=tax_dict.id,
                parent_id=None, code="REGION", label="全部地域", level=1, sort_order=0,
                created_by=user_id(1),
            )
            session.add(root)
            session.flush()
            for i in range(1, 8):
                l1 = TaxItem(
                    org_id=ORG_ID, site_id=SITE_MAIN, dict_id=tax_dict.id,
                    parent_id=root.id, code=f"REG-{i:02d}", label=f"区域 {i:02d}",
                    level=2, sort_order=i, created_by=user_id(1),
                )
                session.add(l1)
                session.flush()
                for j in range(1, 8):
                    session.add(
                        TaxItem(
                            org_id=ORG_ID, site_id=SITE_MAIN, dict_id=tax_dict.id,
                            parent_id=l1.id, code=f"REG-{i:02d}-{j:02d}",
                            label=f"城市 {i:02d}-{j:02d}", level=3, sort_order=j,
                            created_by=user_id(1),
                        )
                    )


def _seed_home(session: Session, counts: dict[str, int]) -> None:
    now = _now()
    for site_id, meta in SITES.items():
        is_main = site_id == SITE_MAIN
        banner_count = counts["banners_main"] if is_main else counts["banners_other"]
        event_count = counts["events_main"] if is_main else counts["events_other"]
        member_count = counts["members_main"] if is_main else counts["members_other"]
        resource_count = counts["resources_main"] if is_main else counts["resources_other"]
        for i in range(1, banner_count + 1):
            session.add(
                CmsBanner(
                    org_id=ORG_ID, site_id=site_id, title=f"{meta['name']}动态 {i:02d}",
                    subtitle="虚构运营位，不含客户信息", status="draft" if i % 9 == 0 else "published",
                    sort_order=i, starts_at=now - timedelta(days=30),
                    ends_at=now + timedelta(days=60), created_by=user_id(1),
                )
            )
        for i in range(1, event_count + 1):
            starts_at = now + timedelta(days=(i - event_count // 2) * 2, hours=(i % 6))
            session.add(
                SiteEvent(
                    org_id=ORG_ID, site_id=site_id, title=f"{meta['name']}活动 {i:03d}",
                    event_type=("salon", "training", "match")[i % 3], starts_at=starts_at,
                    ends_at=starts_at + timedelta(hours=2),
                    location=f"{meta['name']} 多功能厅", status="published", created_by=user_id(1),
                )
            )
        for i in range(1, member_count + 1):
            session.add(
                MemberCompany(
                    org_id=ORG_ID, site_id=site_id, name=f"虚构会员企业 {i:03d}",
                    industry_code=f"IND-{i % 10 + 1:02d}-{i % 10 + 1:02d}",
                    tier=("gold", "silver", "regular")[i % 3], score=100 - i % 90,
                    status="active", created_by=user_id(1),
                )
            )
        for i in range(1, resource_count + 1):
            session.add(
                ResourceListing(
                    org_id=ORG_ID, site_id=site_id, title=f"虚构资源 {i:03d}",
                    category=("service", "capital", "channel")[i % 3], status="active",
                    price_cents=100000 + (i % 50) * 1000, created_by=user_id(1),
                )
            )


def _seed_iam(session: Session, counts: dict[str, int]) -> None:
    roles: dict[str, IamRole] = {}
    for code in ROLE_CODES:
        role = IamRole(
            org_id=ORG_ID, site_id=SITE_MAIN, code=code, name=code.replace("_", " ").title(),
            created_by=user_id(1),
        )
        session.add(role)
        session.flush()
        roles[code] = role
    for index, role in enumerate(roles.values()):
        codes = [
            PERMISSION_CODES[(index * 4 + offset) % len(PERMISSION_CODES)]
            for offset in range(12)
        ]
        for perm_code in codes:
            session.add(
                IamRolePermission(
                    org_id=ORG_ID, site_id=SITE_MAIN, role_id=role.id,
                    perm_code=perm_code, effect="allow", created_by=user_id(1),
                )
            )

    for n in range(1, counts["users"] + 1):
        uid = USER_IDS[n - 1]
        session.add(
            IamUser(
                id=uid, org_id=ORG_ID, site_id=SITE_MAIN, code=f"U{n:04d}",
                display_name=f"演示用户 {n:02d}", status="active", created_by=user_id(1),
            )
        )
        assigned = [ROLE_CODES[(n - 1) % len(ROLE_CODES)]]
        if n % 3 == 0:
            assigned.append(ROLE_CODES[(n * 2) % len(ROLE_CODES)])
        for role_code in sorted(set(assigned)):
            session.add(
                IamUserRole(
                    org_id=ORG_ID, site_id=SITE_MAIN, user_id=uid,
                    role_id=roles[role_code].id, enabled=True, created_by=user_id(1),
                )
            )


def _seed_dashboard(session: Session, counts: dict[str, int]) -> None:
    for site_id, meta in SITES.items():
        is_main = site_id == SITE_MAIN
        opps = counts["opps_main"] if is_main else counts["opps_other"]
        contracts = counts["contracts_main"] if is_main else counts["contracts_other"]
        receivables = counts["receivables_main"] if is_main else counts["receivables_other"]

        session.execute(
            text(
                """
                INSERT INTO crm_opportunities
                    (id, org_id, site_id, name, stage, amount_cents, expected_close_date,
                     owner_id, status, created_at, created_by, updated_at, deleted_at)
                SELECT gen_random_uuid(), :org_id, :site_id,
                       '虚构商机 ' || :tag || '-' || g,
                       (ARRAY['lead','qualified','proposal','negotiation','won','lost'])[1 + (g % 6)],
                       (500000 + (g % 2000) * 10000)::bigint,
                       (date_trunc('month', current_date) - ((g % 6) * interval '1 month')
                         + ((g % 27) * interval '1 day'))::date,
                       :owner, 'open', now(), :creator, now(), NULL
                FROM generate_series(1, :n) AS g
                """
            ),
            {
                "org_id": ORG_ID, "site_id": site_id, "tag": meta["code"], "n": opps,
                "owner": user_id((opps % 40) + 1), "creator": user_id(1),
            },
        )

        session.execute(
            text(
                """
                INSERT INTO crm_contracts
                    (id, org_id, site_id, contract_no, status, amount_cents, signed_at,
                     expires_at, created_at, created_by, updated_at, deleted_at)
                SELECT gen_random_uuid(), :org_id, :site_id,
                       'HT-' || :tag || '-' || lpad(g::text, 5, '0'),
                       (ARRAY['active','active','active','expired','terminated'])[1 + (g % 5)],
                       (1000000 + (g % 3000) * 20000)::bigint,
                       now() - ((g % 540) * interval '1 day'),
                       now() + (((g % 24) - 6) * interval '1 month'),
                       now(), :creator, now(), NULL
                FROM generate_series(1, :n) AS g
                """
            ),
            {
                "org_id": ORG_ID, "site_id": site_id, "tag": meta["code"], "n": contracts,
                "creator": user_id(1),
            },
        )

        session.execute(
            text(
                """
                INSERT INTO fin_receivables
                    (id, org_id, site_id, contract_no, period_no, amount_cents,
                     received_cents, due_date, status, created_at, created_by, updated_at, deleted_at)
                SELECT gen_random_uuid(), :org_id, :site_id,
                       'HT-' || :tag || '-' || lpad((g % 1000)::text, 5, '0'),
                       to_char(date_trunc('month', current_date) - ((g % 12) * interval '1 month'),
                               'YYYY-MM'),
                       (300000 + (g % 500) * 5000)::bigint,
                       CASE WHEN g % 4 = 0 THEN (300000 + (g % 500) * 5000)::bigint
                            WHEN g % 4 = 1 THEN 0::bigint
                            ELSE ((300000 + (g % 500) * 5000) / 2)::bigint END,
                       (date_trunc('month', current_date) - ((g % 12) * interval '1 month')
                         + interval '20 day')::date,
                       CASE WHEN g % 4 = 0 THEN 'received'
                            WHEN g % 4 = 1 THEN 'pending' ELSE 'partial' END,
                       now(), :creator, now(), NULL
                FROM generate_series(1, :n) AS g
                """
            ),
            {
                "org_id": ORG_ID, "site_id": site_id, "tag": meta["code"], "n": receivables,
                "creator": user_id(1),
            },
        )


def seed_dataset(session: Session, profile: str = "perf") -> dict[str, Any]:
    counts = PROFILES[profile]
    _seed_tax(session, counts)
    _seed_home(session, counts)
    _seed_iam(session, counts)
    _seed_dashboard(session, counts)
    session.flush()
    session.execute(text("ANALYZE"))
    return {
        "profile": profile,
        "counts": counts,
        "org_id": str(ORG_ID),
        "sites": {meta["code"]: str(site_id) for site_id, meta in SITES.items()},
    }


def create_schema() -> None:
    from app.db import engine

    Base.metadata.create_all(engine)


def reset_schema() -> None:
    from app.db import engine

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def seed_test_data(session: Session) -> dict[str, Any]:
    return seed_dataset(session, "test")
