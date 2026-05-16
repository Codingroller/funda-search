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


@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        user = result.scalar_one_or_none()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "ntfy_base_url": settings.ntfy_base_url,
            "message": None,
            "error": None,
        },
    )


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

        error = None
        message = None

        if not user or not verify_password(user.password_hash, current_password):
            error = "Current password is incorrect"
        elif new_password != confirm_password:
            error = "New passwords do not match"
        elif len(new_password) < 8:
            error = "Password must be at least 8 characters"
        else:
            user.password_hash = hash_password(new_password)
            await db.commit()
            message = "Password updated successfully"

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "ntfy_base_url": settings.ntfy_base_url,
            "message": message,
            "error": error,
        },
    )


@router.post("/settings/ntfy-topic", response_class=HTMLResponse)
async def regenerate_topic(request: Request):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        user = result.scalar_one_or_none()
        if user:
            user.ntfy_topic = secrets.token_urlsafe(16)
            await db.commit()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "ntfy_base_url": settings.ntfy_base_url,
            "message": "Topic regenerated. Update your subscription.",
            "error": None,
        },
    )


@router.post("/settings/test-notification", response_class=HTMLResponse)
async def test_notification(request: Request):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        user = result.scalar_one_or_none()

    message = None
    error = None
    if user:
        try:
            await send_ntfy(
                topic=user.ntfy_topic,
                title="Funda Search — Test notification",
                message="Your push notifications are working correctly.",
                priority="high",
            )
            message = "Test notification sent!"
        except Exception as exc:
            error = f"Failed to send: {exc}"

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "ntfy_base_url": settings.ntfy_base_url,
            "message": message,
            "error": error,
        },
    )
