"""业务查询与写入（演示实现）。

每个读函数就是缓存回源函数；每个写函数只做 DB 变更，失效动作由路由在提交后执行。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    CmsBanner,
    CrmContract,
    CrmOpportunity,
    FinReceivable,
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


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


# --------------------------------------------------------------------- 字典树
def load_dict_tree(session: Session, org_id: uuid.UUID, dict_code: str) -> dict[str, Any] | None:
    tax_dict = session.execute(
        select(TaxDict).where(
            TaxDict.org_id == org_id,
            TaxDict.code == dict_code,
            TaxDict.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if tax_dict is None:
        return None

    rows = (
        session.execute(
            select(TaxItem)
            .where(TaxItem.dict_id == tax_dict.id, TaxItem.deleted_at.is_(None))
            .order_by(TaxItem.level, TaxItem.sort_order, TaxItem.code)
        )
        .scalars()
        .all()
    )
    nodes: dict[uuid.UUID, dict[str, Any]] = {}
    for row in rows:
        nodes[row.id] = {
            "id": str(row.id),
            "code": row.code,
            "label": row.label,
            "parent_id": str(row.parent_id) if row.parent_id else None,
            "level": row.level,
            "sort_order": row.sort_order,
            "children": [],
        }
    roots: list[dict[str, Any]] = []
    for row in rows:
        node = nodes[row.id]
        parent = nodes.get(row.parent_id) if row.parent_id else None
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)
    max_updated = max((row.updated_at for row in rows), default=tax_dict.updated_at)
    return {
        "dict_code": tax_dict.code,
        "name": tax_dict.name,
        "item_count": len(rows),
        "updated_at": _iso(max_updated),
        "tree": roots,
    }


def load_dict_item_codes(session: Session, org_id: uuid.UUID, dict_code: str) -> list[str] | None:
    tax_dict = session.execute(
        select(TaxDict.id).where(
            TaxDict.org_id == org_id,
            TaxDict.code == dict_code,
            TaxDict.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if tax_dict is None:
        return None
    return list(
        session.execute(
            select(TaxItem.code).where(
                TaxItem.dict_id == tax_dict,
                TaxItem.deleted_at.is_(None),
            )
        ).scalars()
    )


def load_dict_item(
    session: Session, org_id: uuid.UUID, dict_code: str, item_code: str
) -> dict[str, Any] | None:
    row = session.execute(
        select(TaxItem, TaxDict.code)
        .join(TaxDict, TaxDict.id == TaxItem.dict_id)
        .where(
            TaxDict.org_id == org_id,
            TaxDict.code == dict_code,
            TaxDict.deleted_at.is_(None),
            TaxItem.code == item_code,
            TaxItem.deleted_at.is_(None),
        )
    ).first()
    if row is None:
        return None
    item, dict_code_value = row
    return {
        "dict_code": dict_code_value,
        "item_code": item.code,
        "label": item.label,
        "level": item.level,
        "sort_order": item.sort_order,
        "updated_at": _iso(item.updated_at),
    }


# ------------------------------------------------------------------- 首页聚合
def load_home(session: Session, org_id: uuid.UUID, site_id: uuid.UUID) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    banners = (
        session.execute(
            select(CmsBanner)
            .where(
                CmsBanner.org_id == org_id,
                CmsBanner.site_id == site_id,
                CmsBanner.status == "published",
                CmsBanner.starts_at <= now,
                or_(CmsBanner.ends_at.is_(None), CmsBanner.ends_at >= now),
                CmsBanner.deleted_at.is_(None),
            )
            .order_by(CmsBanner.sort_order, CmsBanner.created_at)
            .limit(5)
        )
        .scalars()
        .all()
    )
    events = (
        session.execute(
            select(SiteEvent)
            .where(
                SiteEvent.org_id == org_id,
                SiteEvent.site_id == site_id,
                SiteEvent.status == "published",
                SiteEvent.starts_at >= now,
                SiteEvent.deleted_at.is_(None),
            )
            .order_by(SiteEvent.starts_at)
            .limit(5)
        )
        .scalars()
        .all()
    )
    members = (
        session.execute(
            select(MemberCompany)
            .where(
                MemberCompany.org_id == org_id,
                MemberCompany.site_id == site_id,
                MemberCompany.status == "active",
                MemberCompany.deleted_at.is_(None),
            )
            .order_by(MemberCompany.tier, MemberCompany.score.desc())
            .limit(5)
        )
        .scalars()
        .all()
    )
    counts = session.execute(
        select(
            select(func.count(MemberCompany.id))
            .where(
                MemberCompany.org_id == org_id,
                MemberCompany.site_id == site_id,
                MemberCompany.status == "active",
                MemberCompany.deleted_at.is_(None),
            )
            .scalar_subquery()
            .label("member_count"),
            select(func.count(SiteEvent.id))
            .where(
                SiteEvent.org_id == org_id,
                SiteEvent.site_id == site_id,
                SiteEvent.status == "published",
                SiteEvent.starts_at >= now,
                SiteEvent.deleted_at.is_(None),
            )
            .scalar_subquery()
            .label("upcoming_event_count"),
            select(func.count(ResourceListing.id))
            .where(
                ResourceListing.org_id == org_id,
                ResourceListing.site_id == site_id,
                ResourceListing.status == "active",
                ResourceListing.deleted_at.is_(None),
            )
            .scalar_subquery()
            .label("resource_count"),
        )
    ).one()

    return {
        "site_id": str(site_id),
        "generated_at": _iso(now),
        "banners": [
            {
                "id": str(row.id),
                "title": row.title,
                "subtitle": row.subtitle,
                "sort_order": row.sort_order,
            }
            for row in banners
        ],
        "upcoming_events": [
            {
                "id": str(row.id),
                "title": row.title,
                "event_type": row.event_type,
                "starts_at": _iso(row.starts_at),
                "location": row.location,
            }
            for row in events
        ],
        "featured_members": [
            {
                "id": str(row.id),
                "name": row.name,
                "industry_code": row.industry_code,
                "tier": row.tier,
                "score": row.score,
            }
            for row in members
        ],
        "counts": {
            "members": int(counts.member_count),
            "upcoming_events": int(counts.upcoming_event_count),
            "resources": int(counts.resource_count),
        },
    }


# ----------------------------------------------------------------- 驾驶舱指标
def _period_range(period: str) -> tuple[date, date]:
    year, month = (int(part) for part in period.split("-"))
    start = date(year, month, 1)
    end = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    return start, end


def load_dashboard(
    session: Session, org_id: uuid.UUID, site_id: uuid.UUID, period: str
) -> dict[str, Any]:
    start, end = _period_range(period)
    opp_rows = session.execute(
        select(
            CrmOpportunity.stage,
            func.count(CrmOpportunity.id),
            func.coalesce(func.sum(CrmOpportunity.amount_cents), 0),
        )
        .where(
            CrmOpportunity.org_id == org_id,
            CrmOpportunity.site_id == site_id,
            CrmOpportunity.deleted_at.is_(None),
            CrmOpportunity.expected_close_date >= start,
            CrmOpportunity.expected_close_date < end,
        )
        .group_by(CrmOpportunity.stage)
        .order_by(CrmOpportunity.stage)
    ).all()

    contract_row = session.execute(
        select(
            func.count(CrmContract.id),
            func.coalesce(func.sum(CrmContract.amount_cents), 0),
            func.count(CrmContract.id).filter(
                CrmContract.status == "active",
                CrmContract.expires_at < datetime.now(timezone.utc) + timedelta(days=30),
            ),
            func.count(CrmContract.id).filter(CrmContract.status == "active"),
        ).where(
            CrmContract.org_id == org_id,
            CrmContract.site_id == site_id,
            CrmContract.deleted_at.is_(None),
        )
    ).one()

    receivable_row = session.execute(
        select(
            func.count(FinReceivable.id),
            func.coalesce(func.sum(FinReceivable.amount_cents), 0),
            func.coalesce(func.sum(FinReceivable.received_cents), 0),
            func.count(FinReceivable.id).filter(
                FinReceivable.due_date < date.today(),
                FinReceivable.received_cents < FinReceivable.amount_cents,
            ),
        ).where(
            FinReceivable.org_id == org_id,
            FinReceivable.site_id == site_id,
            FinReceivable.period_no == period,
            FinReceivable.deleted_at.is_(None),
        )
    ).one()

    opportunity_by_stage = [
        {"stage": row[0], "count": int(row[1]), "amount_cents": int(row[2])}
        for row in opp_rows
    ]
    total_opp_count = sum(item["count"] for item in opportunity_by_stage)
    total_opp_amount = sum(item["amount_cents"] for item in opportunity_by_stage)
    total_receivable = int(receivable_row[1])
    received = int(receivable_row[2])

    return {
        "period": period,
        "site_id": str(site_id),
        "generated_at": _iso(datetime.now(timezone.utc)),
        "opportunities": {
            "total_count": total_opp_count,
            "total_amount_cents": total_opp_amount,
            "by_stage": opportunity_by_stage,
        },
        "contracts": {
            "total_count": int(contract_row[0]),
            "total_amount_cents": int(contract_row[1]),
            "expiring_within_30d": int(contract_row[2]),
            "active_count": int(contract_row[3]),
        },
        "receivables": {
            "plan_count": int(receivable_row[0]),
            "total_amount_cents": total_receivable,
            "received_amount_cents": received,
            "overdue_count": int(receivable_row[3]),
            "collection_rate": round(received / total_receivable, 4) if total_receivable else 0.0,
        },
    }


# ------------------------------------------------------------------- 权限点
def load_permissions(
    session: Session, org_id: uuid.UUID, user: uuid.UUID
) -> dict[str, Any] | None:
    user_row = session.execute(
        select(IamUser).where(
            IamUser.id == user,
            IamUser.org_id == org_id,
            IamUser.status == "active",
            IamUser.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if user_row is None:
        return None
    role_rows = session.execute(
        select(IamRole.code)
        .join(IamUserRole, IamUserRole.role_id == IamRole.id)
        .where(
            IamUserRole.user_id == user,
            IamUserRole.org_id == org_id,
            IamUserRole.enabled.is_(True),
            IamUserRole.deleted_at.is_(None),
            IamRole.deleted_at.is_(None),
        )
        .order_by(IamRole.code)
    ).scalars()
    permissions = list(
        session.execute(
            select(IamRolePermission.perm_code)
            .join(IamUserRole, IamUserRole.role_id == IamRolePermission.role_id)
            .where(
                IamUserRole.user_id == user,
                IamUserRole.org_id == org_id,
                IamUserRole.enabled.is_(True),
                IamUserRole.deleted_at.is_(None),
                IamRolePermission.effect == "allow",
                IamRolePermission.deleted_at.is_(None),
            )
            .distinct()
            .order_by(IamRolePermission.perm_code)
        ).scalars()
    )

    return {
        "user_id": str(user),
        "roles": list(role_rows),
        "permissions": permissions,
        "permission_count": len(permissions),
    }


# --------------------------------------------------------------------- 写入
def update_tax_item_label(
    session: Session, item_id: uuid.UUID, label: str
) -> tuple[TaxItem, str]:
    item = session.get(TaxItem, item_id)
    if item is None or item.deleted_at is not None:
        raise LookupError("TAX_ITEM_NOT_FOUND")
    tax_dict = session.get(TaxDict, item.dict_id)
    if tax_dict is None:
        raise LookupError("TAX_DICT_NOT_FOUND")
    item.label = label
    session.flush()
    return item, tax_dict.code


def create_tax_item(
    session: Session,
    org_id: uuid.UUID,
    site_id: uuid.UUID,
    dict_code: str,
    code: str,
    label: str,
    parent_id: uuid.UUID | None = None,
    sort_order: int = 0,
) -> tuple[TaxItem, str]:
    tax_dict = session.execute(
        select(TaxDict).where(
            TaxDict.org_id == org_id,
            TaxDict.code == dict_code,
            TaxDict.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if tax_dict is None:
        raise LookupError("TAX_DICT_NOT_FOUND")
    level = 1
    if parent_id:
        parent = session.get(TaxItem, parent_id)
        if parent is None or parent.dict_id != tax_dict.id:
            raise LookupError("TAX_PARENT_NOT_FOUND")
        level = parent.level + 1
    item = TaxItem(
        org_id=org_id,
        site_id=site_id,
        dict_id=tax_dict.id,
        parent_id=parent_id,
        code=code,
        label=label,
        level=level,
        sort_order=sort_order,
    )
    session.add(item)
    session.flush()
    return item, tax_dict.code


def update_banner(
    session: Session, banner_id: uuid.UUID, title: str | None, status: str | None
) -> tuple[CmsBanner, uuid.UUID, uuid.UUID]:
    banner = session.get(CmsBanner, banner_id)
    if banner is None or banner.deleted_at is not None:
        raise LookupError("BANNER_NOT_FOUND")
    if title is not None:
        banner.title = title
    if status is not None:
        banner.status = status
    session.flush()
    return banner, banner.org_id, banner.site_id


def add_receivable(
    session: Session,
    org_id: uuid.UUID,
    site_id: uuid.UUID,
    period: str,
    amount_cents: int,
    received_cents: int,
    due_date: date,
    contract_no: str,
) -> FinReceivable:
    if received_cents > amount_cents:
        raise ValueError("RECEIVED_GT_AMOUNT")
    row = FinReceivable(
        org_id=org_id,
        site_id=site_id,
        contract_no=contract_no,
        period_no=period,
        amount_cents=amount_cents,
        received_cents=received_cents,
        due_date=due_date,
        status="pending" if received_cents < amount_cents else "received",
    )
    session.add(row)
    session.flush()
    return row


def replace_user_roles(
    session: Session, org_id: uuid.UUID, user: uuid.UUID, role_codes: list[str]
) -> tuple[uuid.UUID, list[str]]:
    user_row = session.execute(
        select(IamUser).where(IamUser.id == user, IamUser.org_id == org_id)
    ).scalar_one_or_none()
    if user_row is None:
        raise LookupError("IAM_USER_NOT_FOUND")
    roles = list(
        session.execute(
            select(IamRole).where(
                IamRole.org_id == org_id,
                IamRole.code.in_(role_codes),
                IamRole.deleted_at.is_(None),
            )
        ).scalars()
    )
    known = {role.code for role in roles}
    unknown = sorted(set(role_codes) - known)
    if unknown:
        raise ValueError(f"UNKNOWN_ROLE:{','.join(unknown)}")

    session.execute(
        IamUserRole.__table__.delete().where(
            IamUserRole.org_id == org_id,
            IamUserRole.user_id == user,
        )
    )
    for role in roles:
        session.add(
            IamUserRole(
                org_id=org_id,
                site_id=user_row.site_id,
                user_id=user,
                role_id=role.id,
                enabled=True,
            )
        )
    session.flush()
    return user_row.site_id, sorted(known)
