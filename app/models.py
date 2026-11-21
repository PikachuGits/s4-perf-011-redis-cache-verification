"""演示库表模型。

统一字段：id(UUID) / org_id / site_id / created_at / created_by / updated_at / deleted_at。
金额一律整数「分」。表结构仅服务于本单的缓存验证，不覆盖会员空间平台全量模型。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CommonMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaxDict(CommonMixin, Base):
    __tablename__ = "tax_dicts"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_tax_dicts_org_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TaxItem(CommonMixin, Base):
    __tablename__ = "tax_items"
    __table_args__ = (
        Index("ix_tax_items_dict_parent", "dict_id", "parent_id"),
        Index(
            "uq_tax_items_dict_code_active",
            "dict_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    dict_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tax_dicts.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CmsBanner(CommonMixin, Base):
    __tablename__ = "cms_banners"
    __table_args__ = (Index("ix_cms_banners_site_status", "site_id", "status"),)

    title: Mapped[str] = mapped_column(String(120), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SiteEvent(CommonMixin, Base):
    __tablename__ = "site_events"
    __table_args__ = (Index("ix_site_events_site_start", "site_id", "starts_at"),)

    title: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="published")


class MemberCompany(CommonMixin, Base):
    __tablename__ = "member_companies"
    __table_args__ = (Index("ix_member_companies_site_status", "site_id", "status"),)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    industry_code: Mapped[str] = mapped_column(String(64), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="regular")
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class ResourceListing(CommonMixin, Base):
    __tablename__ = "resource_listings"
    __table_args__ = (Index("ix_resource_listings_site_status", "site_id", "status"),)

    title: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class CrmOpportunity(CommonMixin, Base):
    __tablename__ = "crm_opportunities"
    __table_args__ = (
        Index("ix_crm_opps_site_stage", "site_id", "stage"),
        Index("ix_crm_opps_site_close", "site_id", "expected_close_date"),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    expected_close_date: Mapped[date] = mapped_column(Date, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")


class CrmContract(CommonMixin, Base):
    __tablename__ = "crm_contracts"
    __table_args__ = (
        Index("ix_crm_contracts_site_status", "site_id", "status"),
        Index("ix_crm_contracts_site_expires", "site_id", "expires_at"),
    )

    contract_no: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FinReceivable(CommonMixin, Base):
    __tablename__ = "fin_receivables"
    __table_args__ = (
        Index("ix_fin_receivables_site_period", "site_id", "period_no"),
        Index("ix_fin_receivables_site_due", "site_id", "due_date"),
    )

    contract_no: Mapped[str] = mapped_column(String(64), nullable=False)
    period_no: Mapped[str] = mapped_column(String(7), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")


class IamUser(CommonMixin, Base):
    __tablename__ = "iam_users"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_iam_users_org_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class IamRole(CommonMixin, Base):
    __tablename__ = "iam_roles"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_iam_roles_org_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)


class IamRolePermission(CommonMixin, Base):
    __tablename__ = "iam_role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "perm_code", name="uq_iam_role_perm"),
        Index("ix_iam_role_permissions_role", "role_id"),
    )

    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    perm_code: Mapped[str] = mapped_column(String(80), nullable=False)
    effect: Mapped[str] = mapped_column(String(10), nullable=False, default="allow")


class IamUserRole(CommonMixin, Base):
    __tablename__ = "iam_user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_iam_user_role"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


TABLES = (
    TaxDict, TaxItem, CmsBanner, SiteEvent, MemberCompany, ResourceListing,
    CrmOpportunity, CrmContract, FinReceivable, IamUser, IamRole,
    IamRolePermission, IamUserRole,
)
