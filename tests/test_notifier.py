import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings
from app.db import Base, engine


@pytest.fixture(autouse=True)
async def _db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(autouse=True)
def vapid_settings(monkeypatch):
    monkeypatch.setattr(settings, "vapid_public_key", "test-pub-key")
    monkeypatch.setattr(settings, "vapid_private_key", "test-priv-key")
    monkeypatch.setattr(settings, "vapid_subject", "mailto:test@example.com")


async def _seed_subscriptions(user_id: int, count: int):
    from app.db import AsyncSessionLocal
    from app.models import PushSubscription
    async with AsyncSessionLocal() as db:
        for i in range(count):
            db.add(PushSubscription(
                user_id=user_id,
                endpoint=f"https://push.example.com/endpoint-{user_id}-{i}",
                p256dh=f"p256dh-{i}",
                auth=f"auth-{i}",
            ))
        await db.commit()


async def _seed_user(username: str) -> int:
    from app.db import AsyncSessionLocal
    from app.models import User
    from app.auth import hash_password
    async with AsyncSessionLocal() as db:
        user = User(username=username, password_hash=hash_password("pw"), is_admin=False)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user.id


@patch("app.notifier.webpush")
async def test_dispatches_to_all_subscriptions(mock_webpush):
    from app.notifier import notify_user
    user_id = await _seed_user("push-test-1")
    await _seed_subscriptions(user_id, 2)

    await notify_user(user_id, title="Hi", body="World", url="/")

    assert mock_webpush.call_count == 2
    call_kwargs = mock_webpush.call_args_list[0].kwargs
    import json
    payload = json.loads(call_kwargs["data"])
    assert payload["title"] == "Hi"
    assert payload["url"] == "/"


@patch("app.notifier.webpush")
async def test_no_subscriptions_skips_send(mock_webpush):
    from app.notifier import notify_user
    user_id = await _seed_user("push-test-2")

    await notify_user(user_id, title="Hi", body="World")

    mock_webpush.assert_not_called()


@patch("app.notifier.webpush")
async def test_410_removes_subscription(mock_webpush):
    from app.notifier import notify_user
    from app.db import AsyncSessionLocal
    from app.models import PushSubscription
    from sqlalchemy import select
    from pywebpush import WebPushException

    user_id = await _seed_user("push-test-3")
    await _seed_subscriptions(user_id, 1)

    class FakeResponse:
        status_code = 410

    mock_webpush.side_effect = WebPushException("gone", response=FakeResponse())

    await notify_user(user_id, title="Hi", body="World")

    async with AsyncSessionLocal() as db:
        remaining = (await db.execute(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        )).scalars().all()
    assert remaining == []


@patch("app.notifier.webpush")
async def test_404_removes_subscription(mock_webpush):
    from app.notifier import notify_user
    from app.db import AsyncSessionLocal
    from app.models import PushSubscription
    from sqlalchemy import select
    from pywebpush import WebPushException

    user_id = await _seed_user("push-test-4")
    await _seed_subscriptions(user_id, 1)

    class FakeResponse:
        status_code = 404

    mock_webpush.side_effect = WebPushException("not found", response=FakeResponse())

    await notify_user(user_id, title="Hi", body="World")

    async with AsyncSessionLocal() as db:
        remaining = (await db.execute(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        )).scalars().all()
    assert remaining == []


@patch("app.notifier.webpush")
async def test_transient_error_keeps_subscription(mock_webpush):
    from app.notifier import notify_user
    from app.db import AsyncSessionLocal
    from app.models import PushSubscription
    from sqlalchemy import select
    from pywebpush import WebPushException

    user_id = await _seed_user("push-test-5")
    await _seed_subscriptions(user_id, 1)

    class FakeResponse:
        status_code = 500

    mock_webpush.side_effect = WebPushException("server error", response=FakeResponse())

    await notify_user(user_id, title="Hi", body="World")

    async with AsyncSessionLocal() as db:
        remaining = (await db.execute(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        )).scalars().all()
    assert len(remaining) == 1


@patch("app.notifier.webpush")
async def test_skips_when_no_vapid_keys(mock_webpush, monkeypatch):
    from app.notifier import notify_user
    monkeypatch.setattr(settings, "vapid_private_key", "")

    user_id = await _seed_user("push-test-6")
    await _seed_subscriptions(user_id, 1)

    await notify_user(user_id, title="Hi", body="World")
    mock_webpush.assert_not_called()
