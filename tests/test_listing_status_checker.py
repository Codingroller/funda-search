"""Tests for the liked-listing status checker — focus on the push notification
that fires when a liked listing's status changes."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.db import AsyncSessionLocal, Base, engine
from app.models import LikedListing, User
from app.auth import hash_password


@pytest.fixture(autouse=True)
async def _db():
    # Fresh schema per test: the checker scans ALL liked listings, so leftover
    # rows from a previous test would otherwise leak in and skew the counts.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(autouse=True)
def _no_sleep():
    # The checker sleeps 2 s between listings to yield priority; skip it in tests.
    with patch("app.listing_status_checker.asyncio.sleep", new=AsyncMock()):
        yield


async def _seed_user(username: str) -> int:
    async with AsyncSessionLocal() as db:
        user = User(username=username, password_hash=hash_password("pw"), is_admin=False)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user.id


async def _seed_liked(user_id: int, global_id: str, status: str, **payload) -> None:
    async with AsyncSessionLocal() as db:
        db.add(LikedListing(
            user_id=user_id,
            global_id=global_id,
            listing_status=status,
            payload_json=json.dumps({"global_id": global_id, **payload}),
        ))
        await db.commit()


async def _status_of(user_id: int, global_id: str) -> str | None:
    async with AsyncSessionLocal() as db:
        row = await db.get(LikedListing, (user_id, global_id))
        return row.listing_status if row else None


async def _run(detail: dict):
    """Run the checker with get_listing_detail mocked to return `detail`,
    and notify_user mocked so we can inspect the push. Returns the mock."""
    from app.listing_status_checker import check_liked_listing_statuses
    with patch("app.funda_client.get_listing_detail", new=AsyncMock(return_value=detail)), \
         patch("app.notifier.notify_user", new=AsyncMock()) as mock_notify:
        await check_liked_listing_statuses()
    return mock_notify


async def test_notifies_when_status_changes_to_sold():
    user_id = await _seed_user("status-1")
    await _seed_liked(user_id, "g-sold", "active",
                      title="Dorpsstraat 1", city="Almere", photo_url="/img/abc.jpg")

    mock_notify = await _run({"listing_status": "sold", "labels": ["Verkocht"]})

    mock_notify.assert_awaited_once()
    kwargs = mock_notify.await_args.kwargs
    args = mock_notify.await_args.args
    assert args[0] == user_id
    assert "sold" in kwargs["body"]
    assert "available" in kwargs["body"]          # mentions the previous state
    assert kwargs["url"] == "/listings/g-sold"
    assert kwargs["tag"] == "status-g-sold"
    assert kwargs["image"] == "/img/abc.jpg"
    assert "Dorpsstraat 1" in kwargs["title"]
    assert await _status_of(user_id, "g-sold") == "sold"


async def test_no_notification_when_status_unchanged():
    user_id = await _seed_user("status-2")
    await _seed_liked(user_id, "g-active", "active", title="X", city="Almere")

    mock_notify = await _run({"listing_status": "available", "labels": []})

    mock_notify.assert_not_awaited()
    assert await _status_of(user_id, "g-active") == "active"


async def test_each_liking_user_is_notified():
    u1 = await _seed_user("status-3a")
    u2 = await _seed_user("status-3b")
    await _seed_liked(u1, "g-shared", "active", title="Shared", city="Almere")
    await _seed_liked(u2, "g-shared", "active", title="Shared", city="Almere")

    mock_notify = await _run({"listing_status": "negotiations", "labels": []})

    assert mock_notify.await_count == 2
    notified_users = {call.args[0] for call in mock_notify.await_args_list}
    assert notified_users == {u1, u2}


async def test_removed_listing_notifies():
    from app.listing_status_checker import check_liked_listing_statuses, ListingNotFound
    user_id = await _seed_user("status-4")
    await _seed_liked(user_id, "g-gone", "active", title="Gone", city="Almere")

    boom = AsyncMock(side_effect=ListingNotFound("404"))
    with patch("app.funda_client.get_listing_detail", new=boom), \
         patch("app.notifier.notify_user", new=AsyncMock()) as mock_notify:
        await check_liked_listing_statuses()

    mock_notify.assert_awaited_once()
    assert "removed from Funda" in mock_notify.await_args.kwargs["body"]
    assert await _status_of(user_id, "g-gone") == "removed"
