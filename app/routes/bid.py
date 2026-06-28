import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.auth import require_auth
from app.bid_estimator import compute_bid_estimate, get_cached_estimate, get_estimate_force
from app.db import AsyncSessionLocal
from app.funda_client import get_listing_detail
from app.models import LikedListing, User
from app.templates_env import templates

router = APIRouter()


async def _is_liked(global_id: str, user_id: int) -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(LikedListing).where(
                LikedListing.user_id == user_id,
                LikedListing.global_id == global_id,
            )
        )
        return result.scalar_one_or_none() is not None


@router.get("/listings/{global_id}/bid-estimate/card", response_class=HTMLResponse)
async def bid_estimate_card(
    request: Request,
    global_id: str,
    current_user: User = Depends(require_auth),
    auto_poll: bool = False,
):
    estimate = await get_cached_estimate(global_id)
    try:
        listing = await get_listing_detail(global_id)
    except Exception:
        raise HTTPException(502)
    liked = await _is_liked(global_id, current_user.id)
    # auto_poll lets a non-liked context (e.g. the House info page) still compute.
    if estimate is None and (liked or auto_poll):
        asyncio.create_task(compute_bid_estimate(global_id))
    return templates.TemplateResponse(
        request, "partials/bid_estimate_card.html",
        {"listing": listing, "estimate": estimate, "is_liked": liked, "auto_poll": auto_poll},
    )


@router.post("/listings/{global_id}/bid-estimate/recompute", response_class=HTMLResponse)
async def bid_estimate_recompute(
    request: Request,
    global_id: str,
    current_user: User = Depends(require_auth),
):
    estimate = await get_estimate_force(global_id)
    try:
        listing = await get_listing_detail(global_id)
    except Exception:
        raise HTTPException(502)
    liked = await _is_liked(global_id, current_user.id)
    return templates.TemplateResponse(
        request, "partials/bid_estimate_card.html",
        {"listing": listing, "estimate": estimate, "is_liked": liked},
    )


@router.get("/listings/{global_id}/bid-estimate/rationale", response_class=HTMLResponse)
async def bid_estimate_rationale(
    request: Request,
    global_id: str,
    current_user: User = Depends(require_auth),
):
    estimate = await get_cached_estimate(global_id)
    if not estimate:
        return HTMLResponse("<p>No estimate available yet.</p>")
    return templates.TemplateResponse(
        request, "partials/bid_estimate_rationale.html",
        {"estimate": estimate, "global_id": global_id},
    )
