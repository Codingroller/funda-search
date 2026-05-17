"""
Route-level integration tests.

These spin up the full FastAPI app in-memory via ASGI and hit real HTTP
endpoints, so they catch template rendering errors, wrong TemplateResponse
signatures, missing routes, and broken redirects — the class of bug that
pure unit tests miss.
"""
import pytest
import httpx
from sqlalchemy import select

from app.auth import hash_password
from app.db import AsyncSessionLocal, Base, engine
from app.main import app
from app.models import User

_PASSWORD = "testpassword123"
_USERNAME = "testadmin"
_USERNAME2 = "testuser2"


@pytest.fixture(autouse=True)
async def _db():
    """Create tables and seed one admin user before each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == _USERNAME))
        if not result.scalar_one_or_none():
            db.add(User(
                username=_USERNAME,
                password_hash=hash_password(_PASSWORD),
                ntfy_topic="test-topic",
                is_admin=True,
            ))
            await db.commit()


@pytest.fixture
async def anon():
    """Unauthenticated ASGI client."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    ) as ac:
        yield ac


@pytest.fixture
async def authed():
    """Client authenticated as the admin user."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    ) as ac:
        r = await ac.post("/login", data={"username": _USERNAME, "password": _PASSWORD})
        assert r.status_code == 302, f"Login failed ({r.status_code}): {r.text[:200]}"
        yield ac


@pytest.fixture
async def authed2():
    """Client authenticated as a second non-admin user."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == _USERNAME2))
        if not result.scalar_one_or_none():
            db.add(User(
                username=_USERNAME2,
                password_hash=hash_password(_PASSWORD),
                ntfy_topic="test-topic-2",
                is_admin=False,
            ))
            await db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    ) as ac:
        r = await ac.post("/login", data={"username": _USERNAME2, "password": _PASSWORD})
        assert r.status_code == 302, f"Login failed ({r.status_code}): {r.text[:200]}"
        yield ac


# --- health ---

async def test_healthz(anon):
    r = await anon.get("/healthz")
    assert r.status_code == 200


# --- auth ---

async def test_login_page_renders(anon):
    r = await anon.get("/login")
    assert r.status_code == 200
    assert b"Funda Search" in r.content


async def test_login_wrong_password_returns_401(anon):
    r = await anon.post("/login", data={"username": _USERNAME, "password": "wrong"})
    assert r.status_code == 401
    assert b"Incorrect" in r.content


async def test_login_wrong_username_returns_401(anon):
    r = await anon.post("/login", data={"username": "nobody", "password": _PASSWORD})
    assert r.status_code == 401


async def test_login_correct_credentials_redirects(anon):
    r = await anon.post("/login", data={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 302
    assert r.headers["location"] == "/"


async def test_already_logged_in_skips_login_page(authed):
    r = await authed.get("/login")
    assert r.status_code == 302
    assert r.headers["location"] == "/"


# --- auth wall ---

async def test_root_unauthenticated_redirects_to_login(anon):
    r = await anon.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


async def test_queries_new_unauthenticated_redirects(anon):
    r = await anon.get("/queries/new")
    assert r.status_code == 302


async def test_settings_unauthenticated_redirects(anon):
    r = await anon.get("/settings")
    assert r.status_code == 302


# --- authenticated routes render without 500 ---

async def test_dashboard_renders(authed):
    r = await authed.get("/")
    assert r.status_code == 200
    assert b"Saved Queries" in r.content


async def test_new_query_form_renders(authed):
    r = await authed.get("/queries/new")
    assert r.status_code == 200
    assert b"New Query" in r.content


async def test_settings_renders(authed):
    r = await authed.get("/settings")
    assert r.status_code == 200
    assert b"Push Notifications" in r.content


async def test_nonexistent_query_returns_404(authed):
    r = await authed.get("/queries/9999")
    assert r.status_code == 404


async def test_query_detail_unauthenticated_redirects(anon):
    r = await anon.get("/queries/1")
    assert r.status_code == 302


# --- listing detail ---

_FAKE_LISTING = {
    "global_id": "12345678",
    "url": "https://www.funda.nl/koop/haarlem/huis-12345678/",
    "title": "Coltermanstraat 10R",
    "street": "Coltermanstraat",
    "house_number": "10",
    "house_number_suffix": "R",
    "postcode": "2014 EM",
    "city": "Haarlem",
    "municipality": "Haarlem",
    "neighbourhood": "Garenkokerskwartier",
    "neighbourhood_identifier": "BU03920301",
    "lat": 52.387,
    "lon": 4.629,
    "price": "€ 545.000",
    "price_per_m2": "€ 6.412 / m²",
    "living_area": 85,
    "plot_area": None,
    "rooms_count": 3,
    "bedrooms": 2,
    "energy_label": "B",
    "object_type": "Apartment",
    "house_type": None,
    "construction_year": 1925,
    "description_title": "Prachtig appartement",
    "description": "Ruim appartement in het hart van Haarlem.",
    "photos": [],
    "broker": {"name": "Makelaardij Test", "association": "NVM", "relative_url": None},
    "publication_date": "2025-05-01",
}


async def test_listing_detail_unauthenticated_redirects(anon):
    r = await anon.get("/listings/12345678")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


async def test_listing_detail_renders(authed, monkeypatch):
    import app.routes.queries as qroutes
    async def _fake_detail(gid):
        return _FAKE_LISTING
    async def _fake_cbs(identifier):
        return None
    monkeypatch.setattr(qroutes, "get_listing_detail", _fake_detail)
    monkeypatch.setattr(qroutes, "get_neighbourhood_stats", _fake_cbs)

    r = await authed.get("/listings/12345678")
    assert r.status_code == 200
    assert b"Coltermanstraat 10R" in r.content
    assert b"545.000" in r.content


async def test_listing_detail_with_cbs(authed, monkeypatch):
    import app.routes.queries as qroutes
    def _make_structured(name, residents, wijk_name=None):
        empty = {k: None for k in ["total","men","women","age_0_15","age_15_25","age_25_45","age_45_65","age_65plus","births_total","births_rate","deaths_total","deaths_rate"]}
        empty["total"] = residents
        return {
            "code": "BU03920301", "name": name, "gemeente": "Haarlem",
            "population": empty,
            "marital": {k: None for k in ["unmarried","married","divorced","widowed"]},
            "heritage": {k: None for k in ["total_nl","total_europe","total_outside_europe","born_nl_heritage_nl","born_nl_heritage_europe","born_nl_heritage_outside","born_abroad_heritage_europe","born_abroad_heritage_outside"]},
            "households": {"total": None, "single_person": None, "without_children": None, "with_children": None, "avg_size": None, "density_per_km2": None},
            "housing": {k: None for k in ["total_stock","non_residential","vacant","woz_value_k","pct_owner","pct_rental","pct_rental_corp","pct_rental_other","pct_built_over_10y","pct_built_last_10y","pct_gas_free","pct_gas_heated","new_builds","new_non_residential"]},
            "housing_type": {k: None for k in ["pct_single_family","pct_terraced","pct_corner","pct_semi_detached","pct_detached","pct_apartment"]},
            "energy": {k: None for k in ["avg_electricity_kwh","avg_electricity_return_kwh","avg_gas_m3","pct_district_heating","pct_solar_panels","pct_electric_heating","ev_charge_points"]},
            "education": {k: None for k in ["primary_pupils","secondary_pupils","mbo_students","hbo_students","wo_students","pct_low","pct_mid","pct_high"]},
            "labour": {k: None for k in ["working_population","net_participation_pct","pct_employees","pct_permanent","pct_flex","pct_self_employed"]},
            "income": {k: None for k in ["recipients","avg_per_recipient_k","avg_per_resident_k","pct_lowest_40","pct_highest_20","pct_poverty","pct_near_poverty","avg_standardized_k","pct_hh_lowest_40","pct_hh_highest_20","median_wealth_k"]},
            "benefits": {k: None for k in ["pct_welfare","pct_disability","pct_unemployment","pct_pension"]},
            "care": {k: None for k in ["youth_care_total","pct_youth_care","wmo_total","pct_wmo"]},
            "businesses": {k: None for k in ["total","agriculture","industry","retail_hosp","transport_ict","finance_re","business_svc","gov_edu_health","culture_other"]},
            "mobility": {k: None for k in ["cars_total","cars_petrol","cars_other_fuel","cars_per_household","cars_per_km2","motorcycles"]},
            "proximity": {k: None for k in ["gp_km","supermarket_km","childcare_km","school_km","schools_3km"]},
            "area": {k: None for k in ["total_ha","land_ha","water_ha","postcode","urbanisation","address_density","coverage_pct"]},
        }

    async def _fake_detail(gid):
        return _FAKE_LISTING
    async def _fake_cbs(identifier):
        return {
            "buurt": _make_structured("Garenkokerskwartier", 1985),
            "wijk":  _make_structured("Zijlwegkwartier", 8180),
        }
    monkeypatch.setattr(qroutes, "get_listing_detail", _fake_detail)
    monkeypatch.setattr(qroutes, "get_neighbourhood_stats", _fake_cbs)

    r = await authed.get("/listings/12345678")
    assert r.status_code == 200
    assert b"Garenkokerskwartier" in r.content
    assert b"Zijlwegkwartier" in r.content
    # With the new view-model, residents are formatted with Dutch thousands separator
    assert b"1.985" in r.content


async def test_listing_detail_not_found(authed, monkeypatch):
    import app.routes.queries as qroutes
    async def _raise(gid):
        raise LookupError("not found")
    monkeypatch.setattr(qroutes, "get_listing_detail", _raise)

    r = await authed.get("/listings/bogus")
    assert r.status_code == 404


async def test_listing_detail_slug_identifier_triggers_geocoding(authed, monkeypatch):
    """When neighbourhood_identifier is a Funda URL slug (not a BU code),
    get_buurtcode_from_coords must be called with the listing's lat/lon."""
    import app.routes.queries as qroutes

    slug_listing = {**_FAKE_LISTING, "neighbourhood_identifier": "almere/noorderplassen-w-west"}
    geocode_calls = []

    async def _fake_detail(gid):
        return slug_listing

    async def _fake_geocode(lat, lon):
        geocode_calls.append((lat, lon))
        return "BU00343102"

    async def _fake_cbs(identifier):
        return None

    monkeypatch.setattr(qroutes, "get_listing_detail", _fake_detail)
    monkeypatch.setattr(qroutes, "get_buurtcode_from_coords", _fake_geocode)
    monkeypatch.setattr(qroutes, "get_neighbourhood_stats", _fake_cbs)

    r = await authed.get("/listings/12345678")
    assert r.status_code == 200
    assert len(geocode_calls) == 1
    assert geocode_calls[0] == (pytest.approx(52.387), pytest.approx(4.629))


async def test_listing_detail_bu_identifier_skips_geocoding(authed, monkeypatch):
    """When neighbourhood_identifier is already a valid CBS BU code, geocoding is skipped."""
    import app.routes.queries as qroutes

    geocode_calls = []

    async def _fake_detail(gid):
        return _FAKE_LISTING  # has neighbourhood_identifier="BU03920301"

    async def _fake_geocode(lat, lon):
        geocode_calls.append((lat, lon))
        return "BU03920301"

    async def _fake_cbs(identifier):
        return None

    monkeypatch.setattr(qroutes, "get_listing_detail", _fake_detail)
    monkeypatch.setattr(qroutes, "get_buurtcode_from_coords", _fake_geocode)
    monkeypatch.setattr(qroutes, "get_neighbourhood_stats", _fake_cbs)

    r = await authed.get("/listings/12345678")
    assert r.status_code == 200
    assert geocode_calls == [], "Should not geocode when identifier starts with BU"


# --- query CRUD ---

async def test_query_create_redirects_to_detail(authed, monkeypatch):
    import app.routes.queries as qroutes
    monkeypatch.setattr(qroutes, "add_query_job", lambda *a, **kw: None)
    async def _noop(*a, **kw): pass
    monkeypatch.setattr(qroutes, "run_query_job", _noop)

    r = await authed.post("/queries", data={
        "name": "My Test Query",
        "location": "Amsterdam",
        "category": "buy",
        "sort": "newest",
        "interval_minutes": "60",
        "enabled_val": "1",
    })
    assert r.status_code == 302
    assert r.headers["location"].startswith("/queries/")


async def test_query_create_persists_to_db(authed, monkeypatch):
    import app.routes.queries as qroutes
    from app.models import SavedQuery
    from sqlalchemy import select

    monkeypatch.setattr(qroutes, "add_query_job", lambda *a, **kw: None)

    await authed.post("/queries", data={
        "name": "Persist Test",
        "location": "Utrecht",
        "category": "rent",
        "sort": "newest",
        "interval_minutes": "30",
    })

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SavedQuery).where(SavedQuery.name == "Persist Test"))
        query = result.scalar_one_or_none()

    assert query is not None
    assert query.interval_minutes == 30


async def test_query_delete_removes_query(authed, monkeypatch):
    import app.routes.queries as qroutes
    from app.models import SavedQuery
    from sqlalchemy import select

    monkeypatch.setattr(qroutes, "add_query_job", lambda *a, **kw: None)
    monkeypatch.setattr(qroutes, "remove_query_job", lambda *a, **kw: None)

    await authed.post("/queries", data={
        "name": "Delete Me",
        "location": "Haarlem",
        "category": "buy",
        "sort": "newest",
        "interval_minutes": "60",
    })

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SavedQuery).where(SavedQuery.name == "Delete Me"))
        query = result.scalar_one()

    r = await authed.delete(f"/queries/{query.id}")
    assert r.status_code == 200

    async with AsyncSessionLocal() as db:
        gone = await db.get(SavedQuery, query.id)
    assert gone is None


async def test_query_edit_form_renders(authed, monkeypatch):
    import app.routes.queries as qroutes
    from app.models import SavedQuery
    from sqlalchemy import select

    monkeypatch.setattr(qroutes, "add_query_job", lambda *a, **kw: None)

    await authed.post("/queries", data={
        "name": "Edit Me",
        "location": "Leiden",
        "category": "buy",
        "sort": "newest",
        "interval_minutes": "60",
    })
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SavedQuery).where(SavedQuery.name == "Edit Me"))
        query = result.scalar_one()

    r = await authed.get(f"/queries/{query.id}/edit")
    assert r.status_code == 200
    assert b"Edit Me" in r.content


async def test_query_toggle_flips_enabled(authed, monkeypatch):
    import app.routes.queries as qroutes
    from app.models import SavedQuery
    from sqlalchemy import select

    monkeypatch.setattr(qroutes, "add_query_job", lambda *a, **kw: None)
    monkeypatch.setattr(qroutes, "remove_query_job", lambda *a, **kw: None)

    await authed.post("/queries", data={
        "name": "Toggle Me",
        "location": "Rotterdam",
        "category": "buy",
        "sort": "newest",
        "interval_minutes": "60",
    })
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SavedQuery).where(SavedQuery.name == "Toggle Me"))
        query = result.scalar_one()
    original_state = query.enabled

    r = await authed.post(f"/queries/{query.id}/toggle")
    assert r.status_code == 200

    async with AsyncSessionLocal() as db:
        updated = await db.get(SavedQuery, query.id)
    assert updated.enabled == (not original_state)


async def test_listing_card_contains_view_details_link(authed, monkeypatch):
    """The query detail page should render View details links on listing cards."""
    import app.routes.queries as qroutes
    import json
    from app.models import SavedQuery, RunLog
    from datetime import datetime

    monkeypatch.setattr(qroutes, "add_query_job", lambda *a, **kw: None)

    await authed.post("/queries", data={
        "name": "Card Link Test",
        "location": "Almere",
        "category": "buy",
        "sort": "newest",
        "interval_minutes": "60",
    })
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(select(SavedQuery).where(SavedQuery.name == "Card Link Test"))
        query = result.scalar_one()
        now = datetime.utcnow()
        db.add(RunLog(
            query_id=query.id,
            started_at=now,
            finished_at=now,
            status="ok",
            result_count=1,
            new_count=1,
            new_listings_json=json.dumps([{
                "global_id": "99887766",
                "url": "https://funda.nl/1",
                "title": "Teststraat 1",
                "city": "Almere",
                "price": "€ 300.000",
                "price_per_m2": None,
                "living_area": 80,
                "rooms_count": 3,
                "bedrooms": 2,
                "energy_label": "B",
                "photo_url": None,
                "publication_date": None,
            }]),
        ))
        await db.commit()

    r = await authed.get(f"/queries/{query.id}")
    assert r.status_code == 200
    assert b"/listings/99887766" in r.content


# --- multi-user isolation ---

async def test_user2_cannot_see_user1_queries(authed, authed2, monkeypatch):
    """Queries created by user1 are invisible to user2."""
    import app.routes.queries as qroutes
    monkeypatch.setattr(qroutes, "add_query_job", lambda *a, **kw: None)

    await authed.post("/queries", data={
        "name": "User1 Private Query",
        "location": "Rotterdam",
        "category": "buy",
        "sort": "newest",
        "interval_minutes": "60",
    })

    r = await authed2.get("/")
    assert r.status_code == 200
    assert b"User1 Private Query" not in r.content


async def test_user2_cannot_edit_user1_query(authed, authed2, monkeypatch):
    """User2 gets 404 when trying to edit user1's query."""
    import app.routes.queries as qroutes
    from app.models import SavedQuery

    monkeypatch.setattr(qroutes, "add_query_job", lambda *a, **kw: None)

    await authed.post("/queries", data={
        "name": "Exclusive Query",
        "location": "Leiden",
        "category": "buy",
        "sort": "newest",
        "interval_minutes": "60",
    })
    async with AsyncSessionLocal() as db:
        q = (await db.execute(select(SavedQuery).where(SavedQuery.name == "Exclusive Query"))).scalar_one()

    r = await authed2.get(f"/queries/{q.id}/edit")
    assert r.status_code == 404


async def test_query_has_user_id_set(authed, monkeypatch):
    """Queries created via the route have user_id matching the logged-in user."""
    import app.routes.queries as qroutes
    from app.models import SavedQuery

    monkeypatch.setattr(qroutes, "add_query_job", lambda *a, **kw: None)

    await authed.post("/queries", data={
        "name": "Ownership Test",
        "location": "Haarlem",
        "category": "buy",
        "sort": "newest",
        "interval_minutes": "60",
    })
    async with AsyncSessionLocal() as db:
        q = (await db.execute(select(SavedQuery).where(SavedQuery.name == "Ownership Test"))).scalar_one()
        user = (await db.execute(select(User).where(User.username == _USERNAME))).scalar_one()

    assert q.user_id == user.id


# --- admin routes ---

async def test_admin_panel_accessible_to_admin(authed):
    r = await authed.get("/admin")
    assert r.status_code == 200
    assert b"Invite new user" in r.content
    assert _USERNAME.encode() in r.content


async def test_admin_panel_forbidden_to_non_admin(authed2):
    r = await authed2.get("/admin")
    assert r.status_code == 403


async def test_admin_generate_invite(authed):
    r = await authed.post("/admin/invite")
    assert r.status_code == 200
    assert b"/signup/" in r.content


async def test_signup_valid_token(authed):
    """A valid invite token lets a new user sign up and get logged in."""
    # Generate token
    r = await authed.post("/admin/invite")
    assert r.status_code == 200
    # Extract token from response
    content = r.text
    idx = content.find("/signup/")
    assert idx != -1
    token = content[idx + len("/signup/"):].split('"')[0].split("<")[0].strip()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    ) as ac:
        r2 = await ac.get(f"/signup/{token}")
        assert r2.status_code == 200
        assert b"Create account" in r2.content

        r3 = await ac.post(f"/signup/{token}", data={
            "username": "newuser",
            "password": "newpassword1",
            "password2": "newpassword1",
        })
        assert r3.status_code == 302
        assert r3.headers["location"] == "/"


async def test_signup_invalid_token_rejected(anon):
    r = await anon.get("/signup/not-a-real-token")
    assert r.status_code == 400


async def test_signup_password_mismatch(authed):
    r = await authed.post("/admin/invite")
    content = r.text
    idx = content.find("/signup/")
    token = content[idx + len("/signup/"):].split('"')[0].split("<")[0].strip()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    ) as ac:
        r2 = await ac.post(f"/signup/{token}", data={
            "username": "newuser2",
            "password": "password1",
            "password2": "different",
        })
        assert r2.status_code == 400
        assert b"do not match" in r2.content


# --- liked checklist ---

async def test_liked_checklist_saves_fields(authed):
    from app.models import LikedListing
    from sqlalchemy import select

    # First like a listing
    await authed.post("/listings/55667788/like")

    # Save checklist fields
    r = await authed.post("/liked/55667788/checklist", data={
        "agent_contacted": "1",
        "viewing_date": "2026-06-01",
        "bid_amount": "450000",
    })
    assert r.status_code == 200

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(LikedListing).where(LikedListing.global_id == "55667788")
        )
        row = result.scalar_one_or_none()

    assert row is not None
    assert row.agent_contacted is True
    assert row.viewing_date == "2026-06-01"
    assert row.bid_amount == 450000


async def test_liked_checklist_unchecked_clears_agent(authed):
    """When checkbox is unchecked the hidden input sends '0' — agent_contacted goes False."""
    from app.models import LikedListing
    from sqlalchemy import select

    await authed.post("/listings/55667789/like")
    # Set it true first
    await authed.post("/liked/55667789/checklist", data={"agent_contacted": "1", "viewing_date": "", "bid_amount": ""})
    # Now uncheck (only hidden "0" is sent)
    await authed.post("/liked/55667789/checklist", data={"agent_contacted": "0", "viewing_date": "", "bid_amount": ""})

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(LikedListing).where(LikedListing.global_id == "55667789")
        )
        row = result.scalar_one_or_none()

    assert row is not None
    assert row.agent_contacted is False
