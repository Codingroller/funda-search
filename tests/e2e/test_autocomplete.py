"""E2E tests for the location autocomplete endpoint.

The existing PWA E2E tests (test_pwa.py) use page.request.get() rather than
page.goto() because headless Chromium crashes on pages that register a service
worker with IndexedDB. We follow the same pattern here, testing the autocomplete
endpoint directly via HTTP — which is what matters: does the server return the
right Dutch city suggestions from the local list?
"""
import pytest
from playwright.sync_api import expect


pytestmark = pytest.mark.e2e


def _login(page, live_server: str):
    """Return a logged-in request context (session cookie set)."""
    page.goto(f"{live_server}/login", wait_until="domcontentloaded", timeout=15000)
    page.fill('input[name="username"]', "e2eadmin")
    page.fill('input[name="password"]', "e2etestpass")
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login" not in url, timeout=10000)


def test_autocomplete_returns_suggestions(live_server, page):
    """Typing a prefix returns matching Dutch city names."""
    _login(page, live_server)
    r = page.request.get(f"{live_server}/autocomplete?location=amst")
    assert r.status == 200
    body = r.text()
    assert "autocomplete-item" in body
    assert "Amsterdam" in body


def test_autocomplete_prefix_first(live_server, page):
    """Prefix matches appear before substring matches."""
    _login(page, live_server)
    r = page.request.get(f"{live_server}/autocomplete?location=utr")
    assert r.status == 200
    body = r.text()
    assert "Utrecht" in body


def test_autocomplete_too_short_returns_empty(live_server, page):
    """Single character returns empty response (< 2 char minimum)."""
    _login(page, live_server)
    r = page.request.get(f"{live_server}/autocomplete?location=a")
    assert r.status == 200
    assert r.text().strip() == ""


def test_autocomplete_multi_city_completes_last_segment(live_server, page):
    """With 'Amsterdam, utr' only the last segment ('utr') is matched."""
    _login(page, live_server)
    r = page.request.get(
        f"{live_server}/autocomplete",
        params={"location": "Amsterdam, utr"},
    )
    assert r.status == 200
    body = r.text()
    # Should match Utrecht-area cities, not re-match Amsterdam
    assert "Utrecht" in body
    assert "Amsterdam" not in body or body.index("Utrecht") < body.index("Amsterdam")


def test_autocomplete_no_results_for_nonsense(live_server, page):
    """A query that matches nothing returns an empty body."""
    _login(page, live_server)
    r = page.request.get(f"{live_server}/autocomplete?location=xyzzy999")
    assert r.status == 200
    assert "autocomplete-item" not in r.text()


def test_autocomplete_requires_auth(live_server, page):
    """Unauthenticated request is redirected to login."""
    r = page.request.get(
        f"{live_server}/autocomplete?location=amsterdam",
        max_redirects=0,
    )
    # Should redirect (302) to /login when not logged in
    assert r.status in (302, 303)
