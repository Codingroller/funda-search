import secrets
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.auth import hash_password
from app.db import AsyncSessionLocal
from app.models import InviteToken, User
from app.templates_env import templates

router = APIRouter()


def _invalid(request, token="", error=""):
    return templates.TemplateResponse(
        request, "signup.html", {"token": token, "error": error}, status_code=400
    )


async def _get_valid_token(db, token: str) -> InviteToken | None:
    invite = await db.get(InviteToken, token)
    if not invite or invite.used_at or invite.expires_at < datetime.utcnow():
        return None
    return invite


@router.get("/signup/{token}")
async def signup_get(request: Request, token: str):
    async with AsyncSessionLocal() as db:
        invite = await _get_valid_token(db, token)
    if not invite:
        return _invalid(request, error="This invite link is invalid or has expired.")
    return templates.TemplateResponse(request, "signup.html", {"token": token, "error": None})


@router.post("/signup/{token}")
async def signup_post(
    request: Request,
    token: str,
    username: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
):
    if password != password2:
        return _invalid(request, token, "Passwords do not match.")
    if len(password) < 8:
        return _invalid(request, token, "Password must be at least 8 characters.")
    if len(username) < 2:
        return _invalid(request, token, "Username must be at least 2 characters.")

    async with AsyncSessionLocal() as db:
        invite = await _get_valid_token(db, token)
        if not invite:
            return _invalid(request, error="This invite link is invalid or has expired.")

        existing = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if existing:
            return _invalid(request, token, f"Username '{username}' is already taken.")

        user = User(
            username=username,
            password_hash=hash_password(password),
            ntfy_topic=secrets.token_urlsafe(16),
            is_admin=False,
        )
        db.add(user)
        await db.flush()

        invite.used_at = datetime.utcnow()
        invite.used_by = user.id
        await db.commit()
        user_id = user.id

    request.session["user_id"] = user_id
    return RedirectResponse("/", status_code=302)
