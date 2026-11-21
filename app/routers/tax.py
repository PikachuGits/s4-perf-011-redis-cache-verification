from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.cache import cache, current_cache_state, get_point
from app.cache.registry import AUXILIARY_POINTS, validate_component
from app.db import get_db
from app.deps import RequestContext, request_context
from app.services import load_dict_item, load_dict_item_codes, load_dict_tree

router = APIRouter(prefix="/api/tax", tags=["tax"])


def _bad_key(name: str, value: str) -> HTTPException:
    return HTTPException(422, detail={"code": "INVALID_CACHE_KEY", "field": name, "value": value})


def _ensure_bloom(db: Session, ctx: RequestContext, dict_code: str):
    bloom = cache.bloom_for(str(ctx.org_id), dict_code)
    if not cache.enabled:
        return bloom
    try:
        if not bloom.exists():
            codes = load_dict_item_codes(db, ctx.org_id, dict_code)
            if codes is None:
                raise HTTPException(404, detail={"code": "DICT_NOT_FOUND"})
            bloom.build(codes)
    except HTTPException:
        raise
    except Exception:
        # Redis 故障时跳过 Bloom，直接走负缓存/DB
        return None
    return bloom


@router.get("/dicts/{dict_code}/tree")
def dict_tree(
    dict_code: str,
    ctx: Annotated[RequestContext, Depends(request_context)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
):
    point = get_point("tax.dict.tree")
    try:
        point.key(org_id=str(ctx.org_id), dict_code=dict_code)
    except ValueError as exc:
        raise _bad_key("dict_code", dict_code) from exc
    value = cache.get_or_load(
        point,
        {"org_id": str(ctx.org_id), "dict_code": dict_code},
        lambda: load_dict_tree(db, ctx.org_id, dict_code),
    )
    if value is None:
        raise HTTPException(404, detail={"code": "DICT_NOT_FOUND"})
    response.headers["X-Cache-Point"] = point.code
    return value


@router.get("/dicts/{dict_code}/items/{item_code}")
def dict_item(
    dict_code: str,
    item_code: str,
    ctx: Annotated[RequestContext, Depends(request_context)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
):
    aux = AUXILIARY_POINTS["tax.dict.item.negative"]
    try:
        aux.key(org_id=str(ctx.org_id), dict_code=dict_code, item_code=item_code)
    except ValueError as exc:
        raise _bad_key("item_code", item_code) from exc
    state = current_cache_state()
    bloom = _ensure_bloom(db, ctx, dict_code) if cache.enabled else None
    if bloom is not None:
        try:
            if not bloom.might_contain(item_code):
                cache.note_bloom_reject(aux, state)
                raise HTTPException(404, detail={"code": "ITEM_NOT_FOUND", "guard": "bloom"})
        except HTTPException:
            raise
        except Exception:
            pass
    if cache.get_negative(aux, {"org_id": str(ctx.org_id), "dict_code": dict_code, "item_code": item_code}):
        raise HTTPException(404, detail={"code": "ITEM_NOT_FOUND", "guard": "negative_cache"})
    value = load_dict_item(db, ctx.org_id, dict_code, item_code)
    if value is None:
        cache.set_negative(aux, {"org_id": str(ctx.org_id), "dict_code": dict_code, "item_code": item_code})
        raise HTTPException(404, detail={"code": "ITEM_NOT_FOUND", "guard": "db"})
    response.headers["X-Cache-Point"] = aux.code
    return value
