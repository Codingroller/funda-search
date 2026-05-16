import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.auth import hash_password, require_auth, verify_password
from app.config import settings
from app.db import AsyncSessionLocal
from app.models import User
from app.notifier import send_ntfy
from app.templates_env import templates

router = APIRouter(dependencies=[Depends(require_auth)])


def _settings_response(request, user, message=None, error=None):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "user": user,
            "ntfy_base_url": settings.ntfy_base_url,
            "message": message,
            "error": error,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        user = result.scalar_one_or_none()
    return _settings_response(request, user)


@router.post("/settings/password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        user = result.scalar_one_or_none()

        if not user or not verify_password(user.password_hash, current_password):
            return _settings_response(request, user, error="Current password is incorrect")
        if new_password != confirm_password:
            return _settings_response(request, user, error="New passwords do not match")
        if len(new_password) < 8:
            return _settings_response(request, user, error="Password must be at least 8 characters")

        user.password_hash = hash_password(new_password)
        await db.commit()

    return _settings_response(request, user, message="Password updated successfully")


@router.post("/settings/ntfy-topic", response_class=HTMLResponse)
async def regenerate_topic(request: Request):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        user = result.scalar_one_or_none()
        if user:
            user.ntfy_topic = secrets.token_urlsafe(16)
            await db.commit()
    return _settings_response(request, user, message="Topic regenerated. Update your subscription.")


@router.post("/settings/test-notification", response_class=HTMLResponse)
async def test_notification(request: Request):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        user = result.scalar_one_or_none()

    if not user:
        return _settings_response(request, user, error="No user found")

    try:
        await send_ntfy(
            topic=user.ntfy_topic,
            title="Funda Search — Test notification",
            message="Your push notifications are working correctly.",
            priority="high",
        )
        return _settings_response(request, user, message="Test notification sent!")
    except Exception as exc:
        return _settings_response(request, user, error=f"Failed to send: {exc}")
