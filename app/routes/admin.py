import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app.auth import require_admin
from app.db import AsyncSessionLocal
from app.models import InviteToken, SavedQuery, User
from app.scheduler import remove_query_job
from app.templates_env import templates

router = APIRouter()


async def _admin_ctx(request: Request, current_user: User, **extra):
    async with AsyncSessionLocal() as db:
        users_raw = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
        # Query counts per user
        counts_raw = await db.execute(
            select(SavedQuery.user_id, func.count(SavedQuery.id)).group_by(SavedQuery.user_id)
        )
        query_counts = dict(counts_raw.all())

        tokens = (await db.execute(
            select(InviteToken)
            .where(InviteToken.used_at == None)  # noqa: E711
            .where(InviteToken.expires_at > datetime.utcnow())
            .order_by(InviteToken.created_at.desc())
        )).scalars().all()

    users = [{"user": u, "query_count": query_counts.get(u.id, 0)} for u in users_raw]
    return templates.TemplateResponse(
        request, "admin.html",
        {"users": users, "invite_tokens": tokens, "current_user": current_user, **extra},
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request, current_user: User = Depends(require_admin)):
    return await _admin_ctx(request, current_user)


@router.post("/admin/invite", response_class=HTMLResponse)
async def create_invite(request: Request, current_user: User = Depends(require_admin)):
    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(days=7)
    async with AsyncSessionLocal() as db:
        db.add(InviteToken(token=token, created_by=current_user.id, expires_at=expires))
        await db.commit()
    invite_url = str(request.base_url).rstrip("/") + f"/signup/{token}"
    return await _admin_ctx(request, current_user, invite_url=invite_url)


@router.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
async def delete_user(user_id: int, request: Request, current_user: User = Depends(require_admin)):
    if user_id == current_user.id:
        raise HTTPException(400, detail="Cannot delete your own account")
    async with AsyncSessionLocal() as db:
        target = await db.get(User, user_id)
        if not target:
            raise HTTPException(404)
        # Cancel all APScheduler jobs for this user's queries before DB delete
        queries = (await db.execute(
            select(SavedQuery).where(SavedQuery.user_id == user_id)
        )).scalars().all()
        for q in queries:
            remove_query_job(q.id)
        await db.delete(target)
        await db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/users/{user_id}/toggle-admin", response_class=HTMLResponse)
async def toggle_admin(user_id: int, request: Request, current_user: User = Depends(require_admin)):
    if user_id == current_user.id:
        raise HTTPException(400, detail="Cannot change your own admin status")
    async with AsyncSessionLocal() as db:
        target = await db.get(User, user_id)
        if not target:
            raise HTTPException(404)
        target.is_admin = not target.is_admin
        await db.commit()
    return RedirectResponse("/admin", status_code=303)
