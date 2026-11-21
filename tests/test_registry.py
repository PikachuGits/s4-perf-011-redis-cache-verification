from __future__ import annotations

import pytest

from app.cache.registry import (
    AUXILIARY_POINTS,
    CACHE_POINTS,
    all_points,
    get_point,
)

EXPECTED_TTL = {
    "tax.dict.tree": 900,
    "site.home": 300,
    "dashboard.metrics": 600,
    "iam.perms": 1800,
}

EXPECTED_PATTERN = {
    "tax.dict.tree": "ms:tax:dict:tree:{org_id}:{dict_code}",
    "site.home": "ms:site:home:{org_id}:{site_id}:{date}",
    "dashboard.metrics": "ms:dashboard:metrics:{org_id}:{site_id}:{period}",
    "iam.perms": "ms:iam:perms:{org_id}:{user_id}",
}


def test_exactly_four_business_points():
    assert set(CACHE_POINTS) == set(EXPECTED_TTL)
    assert len(all_points()) == 4


@pytest.mark.parametrize("code", sorted(EXPECTED_TTL))
def test_pattern_and_ttl(code):
    point = get_point(code)
    assert point.pattern == EXPECTED_PATTERN[code]
    assert point.ttl_seconds == EXPECTED_TTL[code]
    assert point.pattern.startswith("ms:")
    low, high = point.ttl_bounds()
    assert 1 <= low < point.ttl_seconds < high


def test_ttl_jitter_bounds():
    point = get_point("site.home")
    for _ in range(200):
        ttl = point.effective_ttl()
        assert 270 <= ttl <= 330


def test_keys_are_deterministic_and_validated():
    point = get_point("tax.dict.tree")
    key1 = point.key(
        org_id="11111111-1111-1111-1111-111111111111", dict_code="industry"
    )
    key2 = point.key(
        org_id="11111111-1111-1111-1111-111111111111", dict_code="industry"
    )
    assert key1 == key2
    assert key1 == "ms:tax:dict:tree:11111111-1111-1111-1111-111111111111:industry"


@pytest.mark.parametrize(
    "code,params",
    [
        ("tax.dict.tree", {"org_id": "not-a-uuid", "dict_code": "industry"}),
        ("tax.dict.tree", {"org_id": "11111111-1111-1111-1111-111111111111", "dict_code": "INDUSTRY"}),
        ("tax.dict.tree", {"org_id": "11111111-1111-1111-1111-111111111111", "dict_code": "a:b"}),
        ("site.home", {"org_id": "11111111-1111-1111-1111-111111111111", "site_id": "x", "date": "2026-11-21"}),
        ("dashboard.metrics", {"org_id": "11111111-1111-1111-1111-111111111111", "site_id": "22222222-2222-2222-2222-222222222222", "period": "2026-13"}),
        ("iam.perms", {"org_id": "11111111-1111-1111-1111-111111111111", "user_id": "../../etc/passwd"}),
    ],
)
def test_invalid_key_components_rejected(code, params):
    with pytest.raises(ValueError):
        get_point(code).key(**params)


def test_key_components_must_match_pattern():
    with pytest.raises(ValueError):
        get_point("tax.dict.tree").key(org_id="11111111-1111-1111-1111-111111111111")
    with pytest.raises(ValueError):
        get_point("tax.dict.tree").key(
            org_id="11111111-1111-1111-1111-111111111111",
            dict_code="industry",
            extra="x",
        )


def test_auxiliary_negative_cache_ttl_is_short():
    point = AUXILIARY_POINTS["tax.dict.item.negative"]
    assert point.ttl_seconds == 30
    assert point.pattern.startswith("ms:tax:dict:item:neg:")
    assert point.jitter_ratio == 0.0
