from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.cache import cache, get_point
from app.db import get_db
from app.deps import RequestContext, request_context
from app.services import load_home

router = APIRouter(prefix="/api/site", tags=["site"])


@router.get("/home")
def site_home(
    ctx: Annotated[RequestContext, Depends(request_context)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
):
    point = get_point("site.home")
    value = cache.get_or_load(
        point,
        {
            "org_id": str(ctx.org_id),
            "site_id": str(ctx.site_id),
            "date": date.today().isoformat(),
        },
        lambda: load_home(db, ctx.org_id, ctx.site_id),
    )
    response.headers["X-Cache-Point"] = point.code
    return value
