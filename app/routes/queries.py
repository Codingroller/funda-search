import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.auth import require_auth
from app.db import AsyncSessionLocal
from app.funda_client import autocomplete as funda_autocomplete
from app.funda_client import search_listings
from app.models import RunLog, SavedQuery, SeenListing
from app.scheduler import add_query_job, remove_query_job, run_query_job
from app.templates_env import templates

router = APIRouter(dependencies=[Depends(require_auth)])

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


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SavedQuery).order_by(SavedQuery.created_at.desc())
        )
        queries = result.scalars().all()
    return templates.TemplateResponse(
        request, "dashboard.html", {"queries": queries, "intervals": INTERVALS}
    )


@router.get("/queries/new", response_class=HTMLResponse)
async def query_new(request: Request):
    return templates.TemplateResponse(
        request,
        "query_form.html",
        {
            "query": None,
            "params": {},
            "intervals": INTERVALS,
            "object_types": OBJECT_TYPES,
            "energy_labels": ENERGY_LABELS,
            "radius_options": RADIUS_OPTIONS,
        },
    )


@router.post("/queries", response_class=HTMLResponse)
async def query_create(
    request: Request,
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
    enabled_val: Optional[str] = Form(None),
):
    enabled = enabled_val is not None
    form_data = await request.form()
    object_type = list(form_data.getlist("object_type"))
    energy_label = list(form_data.getlist("energy_label"))

    params = _build_params(
        location, category, min_price, max_price, min_area, max_area,
        min_rooms, max_rooms, object_type, energy_label, radius_km, sort,
    )

    async with AsyncSessionLocal() as db:
        query = SavedQuery(
            name=name,
            params_json=json.dumps(params),
            interval_minutes=interval_minutes,
            enabled=enabled,
        )
        db.add(query)
        await db.flush()
        query_id = query.id

        try:
            listings = await search_listings(params)
            for listing in listings:
                db.add(SeenListing(query_id=query_id, global_id=listing["global_id"]))
        except Exception:
            pass

        await db.commit()

    if enabled:
        add_query_job(query_id, interval_minutes)

    return RedirectResponse("/", status_code=302)


@router.get("/queries/{query_id}/edit", response_class=HTMLResponse)
async def query_edit(request: Request, query_id: int):
    async with AsyncSessionLocal() as db:
        query = await db.get(SavedQuery, query_id)
    if not query:
        raise HTTPException(404)
    params = json.loads(query.params_json)
    return templates.TemplateResponse(
        request,
        "query_form.html",
        {
            "query": query,
            "params": params,
            "intervals": INTERVALS,
            "object_types": OBJECT_TYPES,
            "energy_labels": ENERGY_LABELS,
            "radius_options": RADIUS_OPTIONS,
        },
    )


@router.post("/queries/{query_id}", response_class=HTMLResponse)
async def query_update(
    request: Request,
    query_id: int,
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
    enabled_val: Optional[str] = Form(None),
):
    enabled = enabled_val is not None
    form_data = await request.form()
    object_type = list(form_data.getlist("object_type"))
    energy_label = list(form_data.getlist("energy_label"))

    params = _build_params(
        location, category, min_price, max_price, min_area, max_area,
        min_rooms, max_rooms, object_type, energy_label, radius_km, sort,
    )

    async with AsyncSessionLocal() as db:
        query = await db.get(SavedQuery, query_id)
        if not query:
            raise HTTPException(404)
        query.name = name
        query.params_json = json.dumps(params)
        query.interval_minutes = interval_minutes
        query.enabled = enabled
        await db.commit()

    if enabled:
        add_query_job(query_id, interval_minutes)
    else:
        remove_query_job(query_id)

    return RedirectResponse("/", status_code=302)


@router.delete("/queries/{query_id}", response_class=HTMLResponse)
async def query_delete(query_id: int):
    async with AsyncSessionLocal() as db:
        query = await db.get(SavedQuery, query_id)
        if not query:
            raise HTTPException(404)
        await db.delete(query)
        await db.commit()
    remove_query_job(query_id)
    return HTMLResponse("")


@router.post("/queries/{query_id}/toggle", response_class=HTMLResponse)
async def query_toggle(request: Request, query_id: int):
    async with AsyncSessionLocal() as db:
        query = await db.get(SavedQuery, query_id)
        if not query:
            raise HTTPException(404)
        query.enabled = not query.enabled
        await db.commit()

    if query.enabled:
        add_query_job(query_id, query.interval_minutes)
    else:
        remove_query_job(query_id)

    return templates.TemplateResponse(
        request, "partials/query_row.html", {"query": query, "intervals": INTERVALS}
    )


@router.post("/queries/{query_id}/run", response_class=HTMLResponse)
async def query_run(query_id: int):
    await run_query_job(query_id)
    return HTMLResponse("", headers={"HX-Refresh": "true"})


@router.get("/queries/{query_id}", response_class=HTMLResponse)
async def query_detail(request: Request, query_id: int):
    async with AsyncSessionLocal() as db:
        query = await db.get(SavedQuery, query_id)
        if not query:
            raise HTTPException(404)
        result = await db.execute(
            select(RunLog)
            .where(RunLog.query_id == query_id)
            .order_by(RunLog.started_at.desc())
            .limit(20)
        )
        runs = result.scalars().all()
    return templates.TemplateResponse(
        request, "query_detail.html", {"query": query, "runs": runs}
    )


@router.get("/autocomplete", response_class=HTMLResponse)
async def autocomplete_endpoint(request: Request, q: str = ""):
    if len(q) < 2:
        return HTMLResponse("")
    suggestions = await funda_autocomplete(q)
    return templates.TemplateResponse(
        request, "partials/autocomplete.html", {"suggestions": suggestions}
    )
