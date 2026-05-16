import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.auth import hash_password, require_auth, verify_password
from app.config import settings
from app.db import AsyncSessionLocal
from app.models import User
from app.notifier import send_ntfy
from app.templates_env import templates

router = APIRouter()


def _resp(request, user, message=None, error=None):
    return templates.TemplateResponse(
        request, "settings.html",
        {"user": user, "ntfy_base_url": settings.ntfy_base_url,
         "message": message, "error": error, "current_user": user},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request, current_user: User = Depends(require_auth)):
    return _resp(request, current_user)


@router.post("/settings/password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    current_user: User = Depends(require_auth),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    if not verify_password(current_user.password_hash, current_password):
        return _resp(request, current_user, error="Current password is incorrect")
    if new_password != confirm_password:
        return _resp(request, current_user, error="New passwords do not match")
    if len(new_password) < 8:
        return _resp(request, current_user, error="Password must be at least 8 characters")

    async with AsyncSessionLocal() as db:
        user = await db.get(User, current_user.id)
        user.password_hash = hash_password(new_password)
        await db.commit()

    return _resp(request, user, message="Password updated successfully")


@router.post("/settings/ntfy-topic", response_class=HTMLResponse)
async def regenerate_topic(request: Request, current_user: User = Depends(require_auth)):
    async with AsyncSessionLocal() as db:
        user = await db.get(User, current_user.id)
        user.ntfy_topic = secrets.token_urlsafe(16)
        await db.commit()
    return _resp(request, user, message="Topic regenerated. Update your subscription.")


@router.post("/settings/test-notification", response_class=HTMLResponse)
async def test_notification(request: Request, current_user: User = Depends(require_auth)):
    try:
        await send_ntfy(
            topic=current_user.ntfy_topic,
            title="Funda Search — Test notification",
            message="Your push notifications are working correctly.",
            priority="high",
        )
        return _resp(request, current_user, message="Test notification sent!")
    except Exception as exc:
        return _resp(request, current_user, error=f"Failed to send: {exc}")
