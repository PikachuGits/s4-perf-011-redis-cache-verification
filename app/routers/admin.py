from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.cache import cache, get_point
from app.cache.registry import (
    AUXILIARY_POINTS,
    CACHE_POINTS,
    auxiliary_points,
    validate_component,
)
from app.config import get_settings
from app.db import global_metrics, get_db
from app.demo import ORG_ID, PERIODS, SITE_B, SITE_C, SITE_MAIN, USER_IDS
from app.deps import RequestContext, require_admin, request_context
from app.services import (
    add_receivable,
    create_tax_item,
    replace_user_roles,
    update_banner,
    update_tax_item_label,
)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class TaxItemPatch(BaseModel):
    label: str = Field(min_length=1, max_length=120)


class TaxItemCreate(BaseModel):
    dict_code: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    parent_id: uuid.UUID | None = None
    sort_order: int = 0


class BannerPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["draft", "published"] | None = None

    @model_validator(mode="after")
    def at_least_one(self) -> "BannerPatch":
        if self.title is None and self.status is None:
            raise ValueError("title 与 status 至少提供一项")
        return self


class ReceivableCreate(BaseModel):
    amount_cents: int = Field(ge=1)
    received_cents: int = Field(default=0, ge=0)
    due_date: date
    contract_no: str = Field(min_length=1, max_length=64)
    period: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")

    @model_validator(mode="after")
    def check_received(self) -> "ReceivableCreate":
        if self.received_cents > self.amount_cents:
            raise ValueError("received_cents 不得大于 amount_cents")
        return self


class RolesPatch(BaseModel):
    role_codes: list[str] = Field(min_length=0, max_length=8)


def _finalize_write(response: Response, commit_perf_ns: int) -> datetime:
    commit_at = datetime.now(timezone.utc)
    response.headers["X-DB-Commit-At"] = commit_at.isoformat()
    return commit_at


def _set_invalidation_headers(response: Response, record: dict, commit_at: datetime) -> None:
    response.headers["X-Invalidation-Ms"] = f"{record.get('lag_ms', 0.0):.3f}"
    response.headers["X-Invalidation-Keys"] = str(record.get("keys", 0))
    response.headers["X-DB-Commit-At"] = commit_at.isoformat()


@router.get("/cache/points")
def cache_points():
    settings = get_settings()
    return {
        "enabled": cache.enabled,
        "ttl_scale": settings.cache_ttl_scale,
        "key_prefix": settings.cache_key_prefix,
        "points": [point.as_dict(settings.cache_ttl_scale) for point in CACHE_POINTS.values()],
        "auxiliary": [point.as_dict(settings.cache_ttl_scale) for point in auxiliary_points()],
        "conventions": {
            "separator": ":",
            "uuid": "小写标准 UUID；非法值返回 422，不进入键",
            "dict_code": "^[a-z][a-z0-9_-]{0,63}$",
            "item_code": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
            "period": "YYYY-MM",
            "date": "YYYY-MM-DD（服务端本地自然日）",
        },
    }


@router.get("/cache/stats")
def cache_stats():
    snapshot = cache.stats_snapshot()
    snapshot["db"] = global_metrics.snapshot()
    snapshot["process"] = {"pid": __import__("os").getpid()}
    return snapshot


@router.post("/cache/reset-stats")
def cache_reset_stats():
    cache.reset_stats()
    global_metrics.reset()
    return {"ok": True}


@router.post("/cache/flush")
def cache_flush():
    deleted = cache.flush_namespace()
    return {"ok": True, "deleted": deleted}


@router.get("/db/stats")
def db_stats():
    return {"db": global_metrics.snapshot(), "cache_enabled": cache.enabled}


@router.get("/context")
def admin_context():
    return {
        "org_id": str(ORG_ID),
        "sites": {"main": str(SITE_MAIN), "north": str(SITE_B), "south": str(SITE_C)},
        "users": [str(user) for user in USER_IDS],
        "periods": PERIODS,
    }


# -------------------------------------------------------------------- 字典树写
@router.patch("/tax/items/{item_id}")
def patch_tax_item(
    item_id: uuid.UUID,
    body: TaxItemPatch,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        item, dict_code = update_tax_item_label(db, item_id, body.label)
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, detail={"code": str(exc)}) from exc
    commit_perf = time.perf_counter_ns()
    commit_at = _finalize_write(response, commit_perf)
    point = get_point("tax.dict.tree")
    record = cache.invalidate_keys(
        [point.key(org_id=str(item.org_id), dict_code=dict_code)],
        commit_perf_ns=commit_perf,
        reason="tax.item.update",
    )
    _set_invalidation_headers(response, record, commit_at)
    return {
        "id": str(item.id),
        "dict_code": dict_code,
        "label": item.label,
        "invalidated": record,
    }


@router.post("/tax/items")
def post_tax_item(
    body: TaxItemCreate,
    response: Response,
    ctx: Annotated[RequestContext, Depends(request_context)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        item, dict_code = create_tax_item(
            db,
            ctx.org_id,
            ctx.site_id,
            body.dict_code,
            body.code,
            body.label,
            body.parent_id,
            body.sort_order,
        )
        db.commit()
    except (LookupError, ValueError) as exc:
        db.rollback()
        raise HTTPException(404, detail={"code": str(exc)}) from exc
    except Exception as exc:  # 唯一约束冲突等
        db.rollback()
        raise HTTPException(409, detail={"code": "TAX_ITEM_CONFLICT", "message": str(exc)}) from exc
    commit_perf = time.perf_counter_ns()
    commit_at = _finalize_write(response, commit_perf)
    point = get_point("tax.dict.tree")
    negative = AUXILIARY_POINTS["tax.dict.item.negative"]
    bloom = cache.bloom_for(str(item.org_id), dict_code)
    keys = [
        point.key(org_id=str(item.org_id), dict_code=dict_code),
        negative.key(org_id=str(item.org_id), dict_code=dict_code, item_code=item.code),
        bloom.key,
    ]
    record = cache.invalidate_keys(keys, commit_perf_ns=commit_perf, reason="tax.item.create")
    _set_invalidation_headers(response, record, commit_at)
    return {"id": str(item.id), "code": item.code, "dict_code": dict_code, "invalidated": record}


# --------------------------------------------------------------------- 首页写
@router.patch("/home/banners/{banner_id}")
def patch_banner(
    banner_id: uuid.UUID,
    body: BannerPatch,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        banner, org_id, site_id = update_banner(db, banner_id, body.title, body.status)
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, detail={"code": str(exc)}) from exc
    commit_perf = time.perf_counter_ns()
    commit_at = _finalize_write(response, commit_perf)
    point = get_point("site.home")
    record = cache.invalidate_keys(
        [point.key(org_id=str(org_id), site_id=str(site_id), date=date.today().isoformat())],
        commit_perf_ns=commit_perf,
        reason="home.banner.update",
    )
    _set_invalidation_headers(response, record, commit_at)
    return {
        "id": str(banner.id),
        "title": banner.title,
        "status": banner.status,
        "invalidated": record,
    }


# ----------------------------------------------------------------- 驾驶舱写
@router.post("/dashboard/receivables")
def post_receivable(
    body: ReceivableCreate,
    response: Response,
    ctx: Annotated[RequestContext, Depends(request_context)],
    db: Annotated[Session, Depends(get_db)],
):
    period = body.period or body.due_date.strftime("%Y-%m")
    try:
        validate_component("period", period)
        row = add_receivable(
            db,
            ctx.org_id,
            ctx.site_id,
            period,
            body.amount_cents,
            body.received_cents,
            body.due_date,
            body.contract_no,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, detail={"code": str(exc)}) from exc
    commit_perf = time.perf_counter_ns()
    commit_at = _finalize_write(response, commit_perf)
    point = get_point("dashboard.metrics")
    record = cache.invalidate_keys(
        [point.key(org_id=str(row.org_id), site_id=str(row.site_id), period=period)],
        commit_perf_ns=commit_perf,
        reason="dashboard.receivable.create",
    )
    _set_invalidation_headers(response, record, commit_at)
    return {"id": str(row.id), "period": period, "invalidated": record}


# ------------------------------------------------------------------- 权限写
@router.patch("/iam/users/{user_id}/roles")
def patch_user_roles(
    user_id: uuid.UUID,
    body: RolesPatch,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        _, roles = replace_user_roles(db, ORG_ID, user_id, body.role_codes)
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, detail={"code": str(exc)}) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, detail={"code": str(exc)}) from exc
    commit_perf = time.perf_counter_ns()
    commit_at = _finalize_write(response, commit_perf)
    point = get_point("iam.perms")
    record = cache.invalidate_keys(
        [point.key(org_id=str(ORG_ID), user_id=str(user_id))],
        commit_perf_ns=commit_perf,
        reason="iam.roles.update",
    )
    _set_invalidation_headers(response, record, commit_at)
    return {"user_id": str(user_id), "roles": roles, "invalidated": record}
