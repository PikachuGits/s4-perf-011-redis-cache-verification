from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.cache import cache, get_point
from app.db import get_db
from app.deps import RequestContext, request_context
from app.services import load_permissions

router = APIRouter(prefix="/api/iam", tags=["iam"])


@router.get("/permissions")
def permission_points(
    ctx: Annotated[RequestContext, Depends(request_context)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
):
    point = get_point("iam.perms")
    value = cache.get_or_load(
        point,
        {"org_id": str(ctx.org_id), "user_id": str(ctx.user_id)},
        lambda: load_permissions(db, ctx.org_id, ctx.user_id),
    )
    if value is None:
        raise HTTPException(404, detail={"code": "IAM_USER_NOT_FOUND"})
    response.headers["X-Cache-Point"] = point.code
    return value
