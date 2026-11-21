from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_real_contact_details_in_fixtures():
    text = (ROOT / "app" / "seed.py").read_text(encoding="utf-8")
    assert not re.search(r"1[3-9]\d{9}", text)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)


def test_demo_names_are_marked_fictional():
    text = (ROOT / "app" / "seed.py").read_text(encoding="utf-8")
    assert "虚构会员企业" in text
    assert "虚构商机" in text
    assert "customer_name" not in text.lower()


def test_admin_token_is_not_hardcoded_in_docs_or_source():
    offenders = []
    for path in list((ROOT / "app").rglob("*.py")) + list((ROOT / "docs").glob("*.md")):
        content = path.read_text(encoding="utf-8", errors="ignore")
        if "test-admin-token" in content and path.name != "conftest.py":
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_all_cache_points_are_documented():
    docs = (ROOT / "docs" / "cache-points.md").read_text(encoding="utf-8")
    for pattern in (
        "ms:tax:dict:tree:{org_id}:{dict_code}",
        "ms:site:home:{org_id}:{site_id}:{date}",
        "ms:dashboard:metrics:{org_id}:{site_id}:{period}",
        "ms:iam:perms:{org_id}:{user_id}",
    ):
        assert pattern in docs
    for ttl in ("900", "300", "600", "1800"):
        assert ttl in docs
