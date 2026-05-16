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
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        yield ac


@pytest.fixture
async def authed():
    """Client with a valid session cookie."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
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
