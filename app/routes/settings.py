from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app.auth import hash_password, require_auth, verify_password
from app.db import AsyncSessionLocal
from app.models import PushSubscription, User
from app.templates_env import templates

router = APIRouter()


async def _resp(request, user, sub_count=0, message=None, error=None):
    return templates.TemplateResponse(
        request, "settings.html",
        {"user": user, "sub_count": sub_count,
         "message": message, "error": error, "current_user": user},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request, current_user: User = Depends(require_auth)):
    async with AsyncSessionLocal() as db:
        count = (await db.execute(
            select(func.count()).where(PushSubscription.user_id == current_user.id)
        )).scalar_one()
    return await _resp(request, current_user, sub_count=count)


@router.post("/settings/password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    current_user: User = Depends(require_auth),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    if not verify_password(current_user.password_hash, current_password):
        return await _resp(request, current_user, error="Current password is incorrect")
    if new_password != confirm_password:
        return await _resp(request, current_user, error="New passwords do not match")
    if len(new_password) < 8:
        return await _resp(request, current_user, error="Password must be at least 8 characters")

    async with AsyncSessionLocal() as db:
        user = await db.get(User, current_user.id)
        user.password_hash = hash_password(new_password)
        await db.commit()

    return await _resp(request, user, message="Password updated successfully")


@router.post("/settings/test-notification", response_class=HTMLResponse)
async def test_notification(request: Request, current_user: User = Depends(require_auth)):
    from app.notifier import notify_user
    async with AsyncSessionLocal() as db:
        count = (await db.execute(
            select(func.count()).where(PushSubscription.user_id == current_user.id)
        )).scalar_one()

    if count == 0:
        return await _resp(
            request, current_user, sub_count=0,
            error="No devices subscribed. Enable notifications on this device first.",
        )

    try:
        await notify_user(
            current_user.id,
            title="Funda Search — Test",
            body="Push notifications are working correctly.",
            url="/settings",
        )
        return await _resp(request, current_user, sub_count=count,
                           message="Test notification sent — check this device.")
    except Exception as exc:
        return await _resp(request, current_user, sub_count=count,
                           error=f"Failed to send: {exc}")
