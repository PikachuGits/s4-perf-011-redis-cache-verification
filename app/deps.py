from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException

from app.config import get_settings


@dataclass(frozen=True)
class RequestContext:
    org_id: uuid.UUID
    site_id: uuid.UUID
    user_id: uuid.UUID


def request_context(
    x_org_id: Annotated[uuid.UUID | None, Header(alias="X-Org-Id")] = None,
    x_site_id: Annotated[uuid.UUID | None, Header(alias="X-Site-Id")] = None,
    x_user_id: Annotated[uuid.UUID | None, Header(alias="X-User-Id")] = None,
) -> RequestContext:
    settings = get_settings()
    return RequestContext(
        org_id=x_org_id or settings.default_org_id,
        site_id=x_site_id or settings.default_site_id,
        user_id=x_user_id or settings.default_user_id,
    )


def require_admin(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    settings = get_settings()
    if not x_admin_token or not hmac.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(
            status_code=401,
            detail={"code": "ADMIN_UNAUTHORIZED", "message": "缺少或错误的 X-Admin-Token"},
        )
