from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.cache import cache, get_point
from app.cache.registry import validate_component
from app.db import get_db
from app.demo import CURRENT_PERIOD
from app.deps import RequestContext, request_context
from app.services import load_dashboard

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/metrics")
def dashboard_metrics(
    ctx: Annotated[RequestContext, Depends(request_context)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
    period: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = CURRENT_PERIOD,
):
    point = get_point("dashboard.metrics")
    try:
        validate_component("period", period)
        point.key(org_id=str(ctx.org_id), site_id=str(ctx.site_id), period=period)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "INVALID_PERIOD"}) from exc
    value = cache.get_or_load(
        point,
        {"org_id": str(ctx.org_id), "site_id": str(ctx.site_id), "period": period},
        lambda: load_dashboard(db, ctx.org_id, ctx.site_id, period),
    )
    response.headers["X-Cache-Point"] = point.code
    return value
