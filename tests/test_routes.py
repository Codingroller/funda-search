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


@pytest.fixture(autouse=True)
async def _db(monkeypatch):
    """Create tables and seed one user before each test in this module."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        if not result.scalar_one_or_none():
            db.add(User(password_hash=hash_password(_PASSWORD), ntfy_topic="test-topic"))
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
    """Client with a valid session cookie."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    ) as ac:
        r = await ac.post("/login", data={"password": _PASSWORD})
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
    r = await anon.post("/login", data={"password": "wrong"})
    assert r.status_code == 401
    assert b"Incorrect password" in r.content


async def test_login_correct_password_redirects(anon):
    r = await anon.post("/login", data={"password": _PASSWORD})
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
    async def _fake_cbs(lat, lon):
        return None
    monkeypatch.setattr(qroutes, "get_listing_detail", _fake_detail)
    monkeypatch.setattr(qroutes, "get_neighbourhood_stats", _fake_cbs)

    r = await authed.get("/listings/12345678")
    assert r.status_code == 200
    assert b"Coltermanstraat 10R" in r.content
    assert b"545.000" in r.content


async def test_listing_detail_with_cbs(authed, monkeypatch):
    import app.routes.queries as qroutes
    async def _fake_detail(gid):
        return _FAKE_LISTING
    async def _fake_cbs(lat, lon):
        return {
            "buurt": {"buurtnaam": "Garenkokerskwartier", "aantal_inwoners": 1985,
                      "aantal_huishoudens": 970, "bevolkingsdichtheid_inwoners_per_km2": 10451,
                      "gemiddelde_huishoudsgrootte": 2.0, "oppervlakte_land_in_ha": 19,
                      "percentage_eenpersoonshuishoudens": 42,
                      "percentage_huishoudens_met_kinderen": 29,
                      "percentage_huishoudens_zonder_kinderen": 29,
                      "percentage_personen_0_tot_15_jaar": 16,
                      "percentage_personen_15_tot_25_jaar": 10,
                      "percentage_personen_25_tot_45_jaar": 26,
                      "percentage_personen_45_tot_65_jaar": 30,
                      "percentage_personen_65_jaar_en_ouder": 18,
                      "percentage_met_herkomstland_nederland": 70,
                      "percentage_met_herkomstland_uit_europa_excl_nl": 15,
                      "percentage_met_herkomstland_buiten_europa": 15},
            "wijk": {"wijknaam": "Zijlwegkwartier", "aantal_inwoners": 8180,
                     "aantal_huishoudens": 4105, "bevolkingsdichtheid_inwoners_per_km2": 12654,
                     "gemiddelde_huishoudsgrootte": 2.0, "oppervlakte_land_in_ha": 65,
                     "percentage_eenpersoonshuishoudens": 44,
                     "percentage_huishoudens_met_kinderen": 31,
                     "percentage_huishoudens_zonder_kinderen": 25,
                     "percentage_personen_0_tot_15_jaar": 16,
                     "percentage_personen_15_tot_25_jaar": 10,
                     "percentage_personen_25_tot_45_jaar": 30,
                     "percentage_personen_45_tot_65_jaar": 29,
                     "percentage_personen_65_jaar_en_ouder": 15,
                     "percentage_met_herkomstland_nederland": 65,
                     "percentage_met_herkomstland_uit_europa_excl_nl": 15,
                     "percentage_met_herkomstland_buiten_europa": 15},
        }
    monkeypatch.setattr(qroutes, "get_listing_detail", _fake_detail)
    monkeypatch.setattr(qroutes, "get_neighbourhood_stats", _fake_cbs)

    r = await authed.get("/listings/12345678")
    assert r.status_code == 200
    assert b"Garenkokerskwartier" in r.content
    assert b"Zijlwegkwartier" in r.content
    assert b"1985" in r.content


async def test_listing_detail_not_found(authed, monkeypatch):
    import app.routes.queries as qroutes
    async def _raise(gid):
        raise LookupError("not found")
    monkeypatch.setattr(qroutes, "get_listing_detail", _raise)

    r = await authed.get("/listings/bogus")
    assert r.status_code == 404
