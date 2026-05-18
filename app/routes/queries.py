import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.auth import require_auth
from app.cbs_client import get_buurtcode_from_coords, get_neighbourhood_stats
from app.cbs_view import build_view
from app.db import AsyncSessionLocal
from app.dutch_cities import search_cities
from app.funda_client import get_listing_detail, search_listings
from app.models import LikedListing, RunLog, SavedQuery, SeenListing, User
from app.scheduler import add_query_job, remove_query_job, run_query_job
from app.templates_env import templates

router = APIRouter()

INTERVALS = [
    (15, "15 minutes"),
    (30, "30 minutes"),
    (60, "1 hour"),
    (180, "3 hours"),
    (360, "6 hours"),
    (720, "12 hours"),
    (1440, "24 hours"),
]

OBJECT_TYPES = [
    ("house", "House"),
    ("apartment", "Apartment"),
    ("parking", "Parking"),
    ("plot", "Plot"),
]

ENERGY_LABELS = ["A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G"]

RADIUS_OPTIONS = [1, 2, 5, 10, 15, 30, 50]


def _build_params(
    location: str,
    category: str,
    min_price: Optional[int],
    max_price: Optional[int],
    min_area: Optional[int],
    max_area: Optional[int],
    min_rooms: Optional[int],
    max_rooms: Optional[int],
    object_type: list[str],
    energy_label: list[str],
    radius_km: Optional[int],
    sort: str,
) -> dict:
    params: dict = {
        "location": [l.strip() for l in location.split(",") if l.strip()],
        "category": category,
        "sort": sort,
    }
    for key, val in [
        ("min_price", min_price),
        ("max_price", max_price),
        ("min_area", min_area),
        ("max_area", max_area),
        ("min_rooms", min_rooms),
        ("max_rooms", max_rooms),
        ("radius_km", radius_km),
    ]:
        if val is not None:
            params[key] = val
    if object_type:
        params["object_type"] = object_type
    if energy_label:
        params["energy_label"] = energy_label
    return params


def _owned_or_404(query: SavedQuery | None, user: User) -> SavedQuery:
    if not query or query.user_id != user.id:
        raise HTTPException(404)
    return query


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, current_user: User = Depends(require_auth)):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SavedQuery)
            .where(SavedQuery.user_id == current_user.id)
            .order_by(SavedQuery.created_at.desc())
        )
        queries = result.scalars().all()
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"queries": queries, "intervals": INTERVALS, "current_user": current_user},
    )


@router.get("/queries/new", response_class=HTMLResponse)
async def query_new(request: Request, current_user: User = Depends(require_auth)):
    return templates.TemplateResponse(
        request, "query_form.html",
        {
            "query": None, "params": {}, "intervals": INTERVALS,
            "object_types": OBJECT_TYPES, "energy_labels": ENERGY_LABELS,
            "radius_options": RADIUS_OPTIONS, "current_user": current_user,
        },
    )


@router.post("/queries", response_class=HTMLResponse)
async def query_create(
    request: Request,
    current_user: User = Depends(require_auth),
    name: str = Form(...),
    location: str = Form(...),
    category: str = Form("buy"),
    min_price: Optional[int] = Form(None),
    max_price: Optional[int] = Form(None),
    min_area: Optional[int] = Form(None),
    max_area: Optional[int] = Form(None),
    min_rooms: Optional[int] = Form(None),
    max_rooms: Optional[int] = Form(None),
    radius_km: Optional[int] = Form(None),
    sort: str = Form("newest"),
    interval_minutes: int = Form(60),
):
    form_data = await request.form()
    object_type = list(form_data.getlist("object_type"))
    energy_label = list(form_data.getlist("energy_label"))

    params = _build_params(
        location, category, min_price, max_price, min_area, max_area,
        min_rooms, max_rooms, object_type, energy_label, radius_km, sort,
    )

    async with AsyncSessionLocal() as db:
        query = SavedQuery(
            user_id=current_user.id,
            name=name,
            params_json=json.dumps(params),
            interval_minutes=interval_minutes,
            enabled=True,
        )
        db.add(query)
        await db.commit()
        query_id = query.id

    add_query_job(query_id, interval_minutes)
    await run_query_job(query_id)  # first run immediately → results visible on redirect

    return RedirectResponse(f"/queries/{query_id}", status_code=302)


@router.get("/queries/{query_id}/edit", response_class=HTMLResponse)
async def query_edit(request: Request, query_id: int, current_user: User = Depends(require_auth)):
    async with AsyncSessionLocal() as db:
        query = _owned_or_404(await db.get(SavedQuery, query_id), current_user)
    params = json.loads(query.params_json)
    return templates.TemplateResponse(
        request, "query_form.html",
        {
            "query": query, "params": params, "intervals": INTERVALS,
            "object_types": OBJECT_TYPES, "energy_labels": ENERGY_LABELS,
            "radius_options": RADIUS_OPTIONS, "current_user": current_user,
        },
    )


@router.post("/queries/{query_id}", response_class=HTMLResponse)
async def query_update(
    request: Request,
    query_id: int,
    current_user: User = Depends(require_auth),
    name: str = Form(...),
    location: str = Form(...),
    category: str = Form("buy"),
    min_price: Optional[int] = Form(None),
    max_price: Optional[int] = Form(None),
    min_area: Optional[int] = Form(None),
    max_area: Optional[int] = Form(None),
    min_rooms: Optional[int] = Form(None),
    max_rooms: Optional[int] = Form(None),
    radius_km: Optional[int] = Form(None),
    sort: str = Form("newest"),
    interval_minutes: int = Form(60),
):
    form_data = await request.form()
    object_type = list(form_data.getlist("object_type"))
    energy_label = list(form_data.getlist("energy_label"))

    params = _build_params(
        location, category, min_price, max_price, min_area, max_area,
        min_rooms, max_rooms, object_type, energy_label, radius_km, sort,
    )

    async with AsyncSessionLocal() as db:
        query = _owned_or_404(await db.get(SavedQuery, query_id), current_user)
        query.name = name
        query.params_json = json.dumps(params)
        query.interval_minutes = interval_minutes
        query.enabled = True
        await db.commit()

    add_query_job(query_id, interval_minutes)

    return RedirectResponse("/", status_code=302)


@router.delete("/queries/{query_id}", response_class=HTMLResponse)
async def query_delete(query_id: int, current_user: User = Depends(require_auth)):
    async with AsyncSessionLocal() as db:
        query = _owned_or_404(await db.get(SavedQuery, query_id), current_user)
        await db.delete(query)
        await db.commit()
    remove_query_job(query_id)
    return HTMLResponse("")


@router.post("/queries/{query_id}/toggle", response_class=HTMLResponse)
async def query_toggle(request: Request, query_id: int, current_user: User = Depends(require_auth)):
    async with AsyncSessionLocal() as db:
        query = _owned_or_404(await db.get(SavedQuery, query_id), current_user)
        query.enabled = not query.enabled
        await db.commit()

    if query.enabled:
        add_query_job(query_id, query.interval_minutes)
    else:
        remove_query_job(query_id)

    return templates.TemplateResponse(
        request, "partials/query_row.html",
        {"query": query, "intervals": INTERVALS, "current_user": current_user},
    )


@router.post("/queries/{query_id}/run", response_class=HTMLResponse)
async def query_run(request: Request, query_id: int, current_user: User = Depends(require_auth)):
    async with AsyncSessionLocal() as db:
        _owned_or_404(await db.get(SavedQuery, query_id), current_user)
    await run_query_job(query_id)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RunLog).where(RunLog.query_id == query_id)
            .order_by(RunLog.started_at.desc()).limit(1)
        )
        run = result.scalar_one_or_none()
    # 0 new listings: inline confirmation + OOB update of the Last search cell
    if run and run.status == "ok" and run.new_count == 0:
        async with AsyncSessionLocal() as db:
            query = await db.get(SavedQuery, query_id)
        last_search = query.last_run_at.strftime('%d %b %H:%M') if query and query.last_run_at else ''
        inline = templates.env.get_template("partials/run_result.html").render({"run": run})
        oob = (
            f'<span id="query-last-search-{query_id}" '
            f'hx-swap-oob="true">{last_search}</span>'
        )
        return HTMLResponse(inline + oob)
    # New listings found or error: refresh so all columns update
    return HTMLResponse("", headers={"HX-Refresh": "true"})


@router.get("/queries/{query_id}", response_class=HTMLResponse)
async def query_detail(request: Request, query_id: int, current_user: User = Depends(require_auth)):
    async with AsyncSessionLocal() as db:
        query = _owned_or_404(await db.get(SavedQuery, query_id), current_user)
        result = await db.execute(
            select(RunLog)
            .where(RunLog.query_id == query_id)
            .order_by(RunLog.started_at.desc())
        )
        runs = result.scalars().all()
        liked_result = await db.execute(
            select(LikedListing.global_id).where(LikedListing.user_id == current_user.id)
        )
        liked_ids = {row[0] for row in liked_result.all()}
    return templates.TemplateResponse(
        request, "query_detail.html",
        {"query": query, "runs": runs, "current_user": current_user, "liked_ids": liked_ids},
    )


@router.get("/autocomplete", response_class=HTMLResponse)
async def autocomplete_endpoint(
    request: Request, location: str = "", current_user: User = Depends(require_auth)
):
    # Complete only the last segment after a comma (supports "Amsterdam, Utr…")
    q = location.split(",")[-1].strip()
    if len(q) < 2:
        return HTMLResponse("")
    suggestions = search_cities(q)
    return templates.TemplateResponse(
        request, "partials/autocomplete.html", {"suggestions": suggestions},
    )


@router.get("/listings/{global_id}", response_class=HTMLResponse)
async def listing_detail_page(
    request: Request, global_id: str, current_user: User = Depends(require_auth)
):
    try:
        listing = await get_listing_detail(global_id)
    except (LookupError, Exception) as exc:
        if isinstance(exc, LookupError):
            raise HTTPException(404)
        raise HTTPException(502, detail="Could not load listing from Funda")

    cbs = None
    identifier = listing.get("neighbourhood_identifier")
    if (not identifier or not str(identifier).upper().startswith("BU")) and listing.get("lat") and listing.get("lon"):
        identifier = await get_buurtcode_from_coords(listing["lat"], listing["lon"])
    if identifier:
        cbs = await get_neighbourhood_stats(identifier)

    view = build_view(cbs) if cbs else None
    back_url = request.headers.get("referer") or "/"
    async with AsyncSessionLocal() as db:
        liked_result = await db.execute(
            select(LikedListing).where(
                LikedListing.user_id == current_user.id,
                LikedListing.global_id == global_id,
            )
        )
        is_liked = liked_result.scalar_one_or_none() is not None
    return templates.TemplateResponse(
        request, "listing_detail.html",
        {"listing": listing, "cbs": cbs, "view": view, "back_url": back_url,
         "current_user": current_user, "is_liked": is_liked},
    )
