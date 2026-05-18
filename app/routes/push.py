from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import require_auth
from app.config import settings
from app.db import AsyncSessionLocal
from app.models import PushSubscription, User

router = APIRouter(prefix="/push")


class SubPayload(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


@router.get("/vapid-public-key", response_class=PlainTextResponse, include_in_schema=False)
async def vapid_public_key():
    return settings.vapid_public_key


@router.post("/subscribe", response_class=JSONResponse)
async def subscribe(
    payload: SubPayload,
    request: Request,
    current_user: User = Depends(require_auth),
):
    ua = request.headers.get("user-agent", "")[:300]
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
        )).scalar_one_or_none()
        if existing:
            existing.user_id = current_user.id
            existing.p256dh = payload.p256dh
            existing.auth = payload.auth
            existing.user_agent = ua
        else:
            db.add(PushSubscription(
                user_id=current_user.id,
                endpoint=payload.endpoint,
                p256dh=payload.p256dh,
                auth=payload.auth,
                user_agent=ua,
            ))
        await db.commit()
    return {"ok": True}


@router.post("/unsubscribe", response_class=JSONResponse)
async def unsubscribe(
    payload: SubPayload,
    current_user: User = Depends(require_auth),
):
    async with AsyncSessionLocal() as db:
        sub = (await db.execute(
            select(PushSubscription).where(
                PushSubscription.endpoint == payload.endpoint,
                PushSubscription.user_id == current_user.id,
            )
        )).scalar_one_or_none()
        if sub:
            await db.delete(sub)
            await db.commit()
    return {"ok": True}
