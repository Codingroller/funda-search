from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.auth import get_current_user, verify_password
from app.db import AsyncSessionLocal
from app.models import User
from app.templates_env import templates

router = APIRouter()


@router.get("/login")
async def login_get(request: Request):
    if await get_current_user(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()

    if user and verify_password(user.password_hash, password):
        request.session["user_id"] = user.id
        return RedirectResponse("/", status_code=302)

    return templates.TemplateResponse(
        request, "login.html",
        {"error": "Incorrect username or password"},
        status_code=401,
    )


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)
