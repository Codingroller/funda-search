import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select

from app.auth import require_auth
from app.bid_estimator import get_cached_estimate
from app.db import AsyncSessionLocal
from app.models import LikedListing, ListingCache, SharingConnection, User
from app.templates_env import templates

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot(cache_payload: dict, global_id: str) -> dict:
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


async def _get_partner_ids(user_id: int, db) -> set[int]:
    """Return user_ids of all accepted sharing partners."""
    result = await db.execute(
        select(SharingConnection).where(
            SharingConnection.status == "accepted",
            or_(
                SharingConnection.from_user_id == user_id,
                SharingConnection.to_user_id == user_id,
            ),
        )
    )
    partners: set[int] = set()
    for row in result.scalars().all():
        partners.add(
            row.to_user_id if row.from_user_id == user_id else row.from_user_id
        )
    return partners


async def _is_partner(current_user_id: int, owner_id: int, db) -> bool:
    result = await db.execute(
        select(SharingConnection).where(
            SharingConnection.status == "accepted",
            or_(
                (SharingConnection.from_user_id == current_user_id)
                & (SharingConnection.to_user_id == owner_id),
                (SharingConnection.from_user_id == owner_id)
                & (SharingConnection.to_user_id == current_user_id),
            ),
        )
    )
    return result.scalar_one_or_none() is not None


async def _sharing_context(current_user: User, db) -> dict:
    """Build the data needed to render the sharing management sections."""
    partner_ids = await _get_partner_ids(current_user.id, db)

    # Pending invites received
    pending_result = await db.execute(
        select(SharingConnection, User.username)
        .join(User, User.id == SharingConnection.from_user_id)
        .where(
            SharingConnection.to_user_id == current_user.id,
            SharingConnection.status == "pending",
        )
    )
    pending_received = [
        {"id": row.SharingConnection.id, "username": row.username}
        for row in pending_result.all()
    ]

    # Pending invites sent (so user knows they're waiting)
    sent_result = await db.execute(
        select(SharingConnection, User.username)
        .join(User, User.id == SharingConnection.to_user_id)
        .where(
            SharingConnection.from_user_id == current_user.id,
            SharingConnection.status == "pending",
        )
    )
    pending_sent = [
        {"id": row.SharingConnection.id, "username": row.username}
        for row in sent_result.all()
    ]

    # Active partners
    active_partners = []
    if partner_ids:
        partners_result = await db.execute(
            select(User).where(User.id.in_(partner_ids))
        )
        active_partners = [
            {"id": u.id, "username": u.username}
            for u in partners_result.scalars().all()
        ]

    return {
        "partner_ids": partner_ids,
        "pending_received": pending_received,
        "pending_sent": pending_sent,
        "active_partners": active_partners,
    }


# ---------------------------------------------------------------------------
# Like toggle
# ---------------------------------------------------------------------------

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
            payload = _snapshot(json.loads(cache_row.payload_json), global_id) if cache_row else {"global_id": global_id}
            db.add(LikedListing(user_id=current_user.id, global_id=global_id, payload_json=json.dumps(payload)))
            is_liked = True

        await db.commit()

    if is_liked:
        from app.bid_estimator import compute_bid_estimate
        asyncio.create_task(compute_bid_estimate(global_id))

    return templates.TemplateResponse(
        request, "partials/like_button.html",
        {"global_id": global_id, "is_liked": is_liked},
    )


# ---------------------------------------------------------------------------
# Save own notes / checklist
# ---------------------------------------------------------------------------

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


@router.post("/liked/{global_id}/checklist", response_class=HTMLResponse)
async def save_checklist(
    request: Request,
    global_id: str,
    current_user: User = Depends(require_auth),
):
    form_data = await request.form()
    agent_contacted = form_data.get("agent_contacted", "0") == "1"
    viewing_date = form_data.get("viewing_date", "").strip() or None
    wlb_raw = form_data.get("walter_living_bid", "").strip()
    walter_living_bid = int(wlb_raw) if wlb_raw.isdigit() else None
    bid_raw = form_data.get("bid_amount", "").strip()
    bid_amount = int(bid_raw) if bid_raw.isdigit() else None

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(LikedListing).where(
                LikedListing.user_id == current_user.id,
                LikedListing.global_id == global_id,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.agent_contacted = agent_contacted
            row.viewing_date = viewing_date
            row.walter_living_bid = walter_living_bid
            row.bid_amount = bid_amount
            await db.commit()
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Edit a partner's notes / checklist (collaborative editing)
# ---------------------------------------------------------------------------

@router.post("/liked/collab/{owner_id}/{global_id}/notes", response_class=HTMLResponse)
async def collab_save_notes(
    request: Request,
    owner_id: int,
    global_id: str,
    current_user: User = Depends(require_auth),
    notes: str = Form(""),
):
    async with AsyncSessionLocal() as db:
        if not await _is_partner(current_user.id, owner_id, db):
            raise HTTPException(403)
        result = await db.execute(
            select(LikedListing).where(
                LikedListing.user_id == owner_id,
                LikedListing.global_id == global_id,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.notes = notes
            await db.commit()
    return HTMLResponse("")


@router.post("/liked/collab/{owner_id}/{global_id}/checklist", response_class=HTMLResponse)
async def collab_save_checklist(
    request: Request,
    owner_id: int,
    global_id: str,
    current_user: User = Depends(require_auth),
):
    form_data = await request.form()
    agent_contacted = form_data.get("agent_contacted", "0") == "1"
    viewing_date = form_data.get("viewing_date", "").strip() or None
    wlb_raw = form_data.get("walter_living_bid", "").strip()
    walter_living_bid = int(wlb_raw) if wlb_raw.isdigit() else None
    bid_raw = form_data.get("bid_amount", "").strip()
    bid_amount = int(bid_raw) if bid_raw.isdigit() else None

    async with AsyncSessionLocal() as db:
        if not await _is_partner(current_user.id, owner_id, db):
            raise HTTPException(403)
        result = await db.execute(
            select(LikedListing).where(
                LikedListing.user_id == owner_id,
                LikedListing.global_id == global_id,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.agent_contacted = agent_contacted
            row.viewing_date = viewing_date
            row.walter_living_bid = walter_living_bid
            row.bid_amount = bid_amount
            await db.commit()
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Sharing invite flow
# ---------------------------------------------------------------------------

@router.post("/liked/invite", response_class=HTMLResponse)
async def send_invite(
    request: Request,
    current_user: User = Depends(require_auth),
    username: str = Form(...),
):
    async with AsyncSessionLocal() as db:
        # Find target user
        target = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        error = None
        if not target:
            error = f"No user '{username}' found."
        elif target.id == current_user.id:
            error = "You can't invite yourself."
        else:
            # Check if connection already exists (any status)
            existing = (await db.execute(
                select(SharingConnection).where(
                    or_(
                        (SharingConnection.from_user_id == current_user.id) & (SharingConnection.to_user_id == target.id),
                        (SharingConnection.from_user_id == target.id) & (SharingConnection.to_user_id == current_user.id),
                    )
                )
            )).scalar_one_or_none()
            if existing:
                if existing.status == "accepted":
                    error = f"You are already sharing with {username}."
                elif existing.status == "pending":
                    error = f"An invite involving {username} is already pending."
                elif existing.status == "declined":
                    # Allow re-invite after decline — update existing row
                    existing.status = "pending"
                    existing.from_user_id = current_user.id
                    existing.to_user_id = target.id
                    existing.created_at = datetime.utcnow()
                    existing.responded_at = None
            else:
                db.add(SharingConnection(from_user_id=current_user.id, to_user_id=target.id))
            if not error:
                await db.commit()

        ctx = await _sharing_context(current_user, db)
        ctx["share_error"] = error
        ctx["current_user"] = current_user
    return templates.TemplateResponse(request, "partials/sharing_section.html", ctx)


@router.post("/liked/invite/{invite_id}/accept", response_class=HTMLResponse)
async def accept_invite(
    request: Request,
    invite_id: int,
    current_user: User = Depends(require_auth),
):
    async with AsyncSessionLocal() as db:
        invite = await db.get(SharingConnection, invite_id)
        if not invite or invite.to_user_id != current_user.id:
            raise HTTPException(404)
        invite.status = "accepted"
        invite.responded_at = datetime.utcnow()
        await db.commit()
    return HTMLResponse("", headers={"HX-Refresh": "true"})


@router.post("/liked/invite/{invite_id}/decline", response_class=HTMLResponse)
async def decline_invite(
    request: Request,
    invite_id: int,
    current_user: User = Depends(require_auth),
):
    async with AsyncSessionLocal() as db:
        invite = await db.get(SharingConnection, invite_id)
        if not invite or invite.to_user_id != current_user.id:
            raise HTTPException(404)
        invite.status = "declined"
        invite.responded_at = datetime.utcnow()
        await db.commit()
        ctx = await _sharing_context(current_user, db)
        ctx["current_user"] = current_user
    return templates.TemplateResponse(request, "partials/sharing_section.html", ctx)


@router.delete("/liked/connection/{partner_id}", response_class=HTMLResponse)
async def stop_sharing(
    request: Request,
    partner_id: int,
    current_user: User = Depends(require_auth),
):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SharingConnection).where(
                SharingConnection.status == "accepted",
                or_(
                    (SharingConnection.from_user_id == current_user.id) & (SharingConnection.to_user_id == partner_id),
                    (SharingConnection.from_user_id == partner_id) & (SharingConnection.to_user_id == current_user.id),
                ),
            )
        )
        conn = result.scalar_one_or_none()
        if conn:
            await db.delete(conn)
            await db.commit()
        ctx = await _sharing_context(current_user, db)
        ctx["current_user"] = current_user
    return templates.TemplateResponse(request, "partials/sharing_section.html", ctx)


# ---------------------------------------------------------------------------
# Main liked page
# ---------------------------------------------------------------------------

@router.get("/liked", response_class=HTMLResponse)
async def liked_page(request: Request, current_user: User = Depends(require_auth)):
    async with AsyncSessionLocal() as db:
        ctx = await _sharing_context(current_user, db)
        all_ids = {current_user.id} | ctx["partner_ids"]

        # Fetch liked listings + username for all users in the group
        liked_result = await db.execute(
            select(LikedListing, User.username)
            .join(User, User.id == LikedListing.user_id)
            .where(LikedListing.user_id.in_(all_ids))
            .order_by(LikedListing.liked_at.desc())
        )

    rows = liked_result.all()
    items_base = [
        {
            "listing": json.loads(row.LikedListing.payload_json),
            "notes": row.LikedListing.notes or "",
            "agent_contacted": row.LikedListing.agent_contacted,
            "viewing_date": row.LikedListing.viewing_date or "",
            "walter_living_bid": row.LikedListing.walter_living_bid,
            "bid_amount": row.LikedListing.bid_amount,
            "liked_at": row.LikedListing.liked_at,
            "global_id": row.LikedListing.global_id,
            "owner_id": row.LikedListing.user_id,
            "owner_username": row.username,
            "is_own": row.LikedListing.user_id == current_user.id,
        }
        for row in rows
    ]

    estimates = await asyncio.gather(
        *[get_cached_estimate(item["global_id"]) for item in items_base]
    )
    items = [dict(item, estimate=est) for item, est in zip(items_base, estimates)]

    ctx.update({"items": items, "current_user": current_user})
    return templates.TemplateResponse(request, "liked.html", ctx)
