import json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.auth import require_auth
from app.db import AsyncSessionLocal
from app.models import LikedListing, ListingCache, User
from app.templates_env import templates

router = APIRouter()


def _snapshot(cache_payload: dict, global_id: str) -> dict:
    """Extract the fields needed to render a liked listing card."""
    photos = cache_payload.get("photos", [])
    return {
        "global_id": global_id,
        "title": cache_payload.get("title"),
        "price": cache_payload.get("price"),
        "price_per_m2": cache_payload.get("price_per_m2"),
        "photo_url": photos[0] if photos else None,
        "url": cache_payload.get("url"),
        "living_area": cache_payload.get("living_area"),
        "rooms_count": cache_payload.get("rooms_count"),
        "energy_label": cache_payload.get("energy_label"),
        "city": cache_payload.get("city"),
        "postcode": cache_payload.get("postcode"),
    }


@router.post("/listings/{global_id}/like", response_class=HTMLResponse)
async def toggle_like(
    request: Request,
    global_id: str,
    current_user: User = Depends(require_auth),
):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(LikedListing).where(
                LikedListing.user_id == current_user.id,
                LikedListing.global_id == global_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            await db.delete(existing)
            is_liked = False
        else:
            cache_row = await db.get(ListingCache, global_id)
            if cache_row:
                payload = _snapshot(json.loads(cache_row.payload_json), global_id)
            else:
                payload = {"global_id": global_id}
            db.add(LikedListing(
                user_id=current_user.id,
                global_id=global_id,
                payload_json=json.dumps(payload),
            ))
            is_liked = True

        await db.commit()

    return templates.TemplateResponse(
        request, "partials/like_button.html",
        {"global_id": global_id, "is_liked": is_liked},
    )


@router.post("/liked/{global_id}/notes", response_class=HTMLResponse)
async def save_notes(
    request: Request,
    global_id: str,
    current_user: User = Depends(require_auth),
    notes: str = Form(""),
):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(LikedListing).where(
                LikedListing.user_id == current_user.id,
                LikedListing.global_id == global_id,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.notes = notes
            await db.commit()
    return HTMLResponse("")


@router.get("/liked", response_class=HTMLResponse)
async def liked_page(request: Request, current_user: User = Depends(require_auth)):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(LikedListing)
            .where(LikedListing.user_id == current_user.id)
            .order_by(LikedListing.liked_at.desc())
        )
        rows = result.scalars().all()

    items = [
        {
            "listing": json.loads(r.payload_json),
            "notes": r.notes or "",
            "liked_at": r.liked_at,
            "global_id": r.global_id,
        }
        for r in rows
    ]
    return templates.TemplateResponse(
        request, "liked.html",
        {"items": items, "current_user": current_user},
    )
