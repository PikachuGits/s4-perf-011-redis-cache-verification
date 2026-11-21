"""固定演示上下文：组织、站点、用户与字典编码。

全部为虚构值（固定 UUID），供压测、测试与文档共用。
"""
from __future__ import annotations

import uuid
from datetime import date

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
SITE_MAIN = uuid.UUID("22222222-2222-2222-2222-222222222222")
SITE_B = uuid.UUID("44444444-4444-4444-4444-444444444444")
SITE_C = uuid.UUID("55555555-5555-5555-5555-555555555555")

SITES = {
    SITE_MAIN: {"code": "main", "name": "主空间"},
    SITE_B: {"code": "north", "name": "北站空间"},
    SITE_C: {"code": "south", "name": "南站空间"},
}

DICT_CODES = ("industry", "tags", "region")

PERMISSION_CODES = (
    "crm.lead.read", "crm.lead.write", "crm.contact.read", "crm.contact.write",
    "crm.opportunity.read", "crm.opportunity.write", "crm.contract.read",
    "crm.contract.write", "crm.receivable.read", "crm.receivable.write",
    "space.room.read", "space.room.write", "space.booking.read", "space.booking.write",
    "event.read", "event.write", "member.read", "member.write",
    "kb.article.read", "kb.article.write", "kb.qa.read", "kb.qa.write",
    "sop.task.read", "sop.task.write", "dashboard.view", "dashboard.export",
    "intel.item.read", "intel.item.write", "iam.role.read", "iam.role.write",
    "audit.log.read", "settings.site.read", "settings.site.write",
    "match.request.read", "match.request.write", "podcast.read",
    "academy.course.read", "academy.course.write", "finance.invoice.read",
    "finance.invoice.write",
)

ROLE_CODES = (
    "org_admin", "sales_lead", "sales_rep", "space_manager",
    "event_operator", "knowledge_editor", "finance_clerk", "auditor",
)


def user_id(n: int) -> uuid.UUID:
    """第 n 个演示用户（n 从 1 开始）。"""
    return uuid.UUID(f"33333333-3333-3333-3333-{n:012d}")


USER_IDS = tuple(user_id(i) for i in range(1, 41))


def periods(count: int = 6, today: date | None = None) -> list[str]:
    """最近 count 个月（含当月），格式 YYYY-MM，从旧到新。"""
    today = today or date.today()
    items: list[str] = []
    year, month = today.year, today.month
    for _ in range(count):
        items.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return list(reversed(items))


CURRENT_PERIOD = periods(1)[0]
PERIODS = periods(6)
