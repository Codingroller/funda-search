"""Tests for the House-info 'recent searches' list (record + dedup + prune)."""

import pytest

from app.db import AsyncSessionLocal, Base, engine
from app.models import HouseInfoSearch, User
from app.routes.house_info import _record_search, _recent_searches, _RECENT_KEEP


async def _setup_user(username: str) -> int:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        u = User(username=username, password_hash="x", is_admin=False)
        db.add(u)
        await db.commit()
        return u.id


@pytest.mark.asyncio
class TestRecentSearches:
    async def test_record_and_list_newest_first(self):
        uid = await _setup_user("recent-a")
        await _record_search(uid, {"pdok_id": "p1", "label": "Prinsengracht 263, Amsterdam"})
        await _record_search(uid, {"pdok_id": "p2", "label": "Damrak 1, Amsterdam"})

        recent = await _recent_searches(uid)
        assert [r["label"] for r in recent] == [
            "Damrak 1, Amsterdam", "Prinsengracht 263, Amsterdam",
        ]
        assert recent[0]["url"] == "/house-info/result?pdok_id=p2"

    async def test_dedup_bumps_existing_to_top(self):
        uid = await _setup_user("recent-b")
        await _record_search(uid, {"pdok_id": "p1", "label": "A 1, Utrecht"})
        await _record_search(uid, {"pdok_id": "p2", "label": "B 2, Utrecht"})
        await _record_search(uid, {"pdok_id": "p1", "label": "A 1, Utrecht"})  # re-search

        recent = await _recent_searches(uid)
        assert [r["label"] for r in recent] == ["A 1, Utrecht", "B 2, Utrecht"]
        # only two rows — no duplicate for p1
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            rows = (await db.execute(
                select(HouseInfoSearch).where(HouseInfoSearch.user_id == uid)
            )).scalars().all()
        assert len(rows) == 2

    async def test_freetext_uses_query_url(self):
        uid = await _setup_user("recent-c")
        await _record_search(uid, {"pdok_id": None, "label": "Ergens 5, Dorp"})
        recent = await _recent_searches(uid)
        assert recent[0]["url"] == "/house-info/result?q=Ergens%205%2C%20Dorp"

    async def test_limited_to_five(self):
        uid = await _setup_user("recent-d")
        for i in range(8):
            await _record_search(uid, {"pdok_id": f"p{i}", "label": f"Addr {i}"})
        recent = await _recent_searches(uid)
        assert len(recent) == 5
        assert recent[0]["label"] == "Addr 7"   # newest

    async def test_pruned_to_keep_cap(self):
        uid = await _setup_user("recent-e")
        for i in range(_RECENT_KEEP + 5):
            await _record_search(uid, {"pdok_id": f"q{i}", "label": f"Row {i}"})
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select, func
            total = (await db.execute(
                select(func.count()).select_from(HouseInfoSearch)
                .where(HouseInfoSearch.user_id == uid)
            )).scalar_one()
        assert total == _RECENT_KEEP

    async def test_missing_label_is_ignored(self):
        uid = await _setup_user("recent-f")
        await _record_search(uid, {"pdok_id": "p1", "label": None})
        assert await _recent_searches(uid) == []
