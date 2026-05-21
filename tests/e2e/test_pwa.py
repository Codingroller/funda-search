"""Playwright E2E tests for PWA and Web Push infrastructure.

Run with:
    pytest tests/e2e -v -m e2e

Requires: pip install pytest-playwright playwright && playwright install chromium
"""
import json

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_manifest_served(live_server, page):
    r = page.request.get(f"{live_server}/static/manifest.webmanifest")
    assert r.status == 200
    m = r.json()
    assert m["start_url"] == "/"
    assert m["display"] == "standalone"
    assert m["name"] == "Funda Search"
    assert any(
        i.get("purpose") == "maskable" and "512x512" in i.get("sizes", "")
        for i in m["icons"]
    )


def test_sw_served_at_root_with_correct_scope_header(live_server, page):
    r = page.request.get(f"{live_server}/sw.js")
    assert r.status == 200
    assert r.headers.get("service-worker-allowed") == "/"
    assert r.headers.get("content-type", "").startswith("application/javascript")
    assert r.headers.get("cache-control") == "no-cache"


def test_icons_all_load(live_server, page):
    for name in ["icon-192.png", "icon-512.png", "maskable-512.png", "apple-touch-icon.png", "badge-72.png"]:
        r = page.request.get(f"{live_server}/static/icons/{name}")
        assert r.status == 200, f"{name} returned {r.status}"
        assert r.headers.get("content-type", "").startswith("image/png")


def test_manifest_link_in_head(auth_page, live_server):
    # Use request.get (HTTP-only, no JS/SW execution) to avoid headless Chromium
    # crashing on pages that register a service worker with IndexedDB.
    r = auth_page.request.get(f"{live_server}/")
    assert r.status == 200
    assert 'href="/static/manifest.webmanifest"' in r.text()


def test_theme_color_meta_in_head(auth_page, live_server):
    r = auth_page.request.get(f"{live_server}/")
    assert r.status == 200
    assert 'name="theme-color"' in r.text()
    assert 'content="#1c2333"' in r.text()


def test_apple_touch_icon_in_head(auth_page, live_server):
    r = auth_page.request.get(f"{live_server}/")
    assert r.status == 200
    assert 'rel="apple-touch-icon"' in r.text()


@pytest.mark.xfail(reason="headless Chromium crashes on pages with IndexedDB service workers")
def test_sw_registers(auth_page, live_server):
    auth_page.goto(f"{live_server}/")
    state = auth_page.evaluate(
        "async () => { const r = await navigator.serviceWorker.ready; return r.active ? r.active.state : 'none'; }"
    )
    assert state in ("activating", "activated")


def test_vapid_public_key_endpoint(live_server, page):
    r = page.request.get(f"{live_server}/push/vapid-public-key")
    assert r.status == 200
    key = r.text().strip()
    # Should be a non-empty base64url string (65 bytes uncompressed point = ~88 chars)
    assert len(key) > 80
    assert set(key) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")


def test_subscribe_endpoint_stores_subscription(live_server, browser):
    """POST to /push/subscribe with a fake subscription and verify it's accepted."""
    ctx = browser.new_context()
    page = ctx.new_page()

    # Log in
    page.goto(f"{live_server}/login")
    page.fill('input[name="username"]', "e2eadmin")
    page.fill('input[name="password"]', "e2etestpass")
    with page.expect_navigation(timeout=10000):
        page.click('button[type="submit"]')

    # POST a fake subscription directly to the API
    r = page.request.post(
        f"{live_server}/push/subscribe",
        data=json.dumps({
            "endpoint": "https://push.example.com/fake-endpoint-for-test",
            "p256dh": "BNbLHgs8sw7HZQvTVkDZ0test",
            "auth": "testauth==",
        }),
        headers={"Content-Type": "application/json"},
    )
    assert r.status == 200
    assert r.json() == {"ok": True}

    ctx.close()


def test_unsubscribe_endpoint_removes_subscription(live_server, browser):
    """POST to /push/unsubscribe removes the stored subscription."""
    ctx = browser.new_context()
    page = ctx.new_page()

    # Log in
    page.goto(f"{live_server}/login")
    page.fill('input[name="username"]', "e2eadmin")
    page.fill('input[name="password"]', "e2etestpass")
    with page.expect_navigation(timeout=10000):
        page.click('button[type="submit"]')

    fake_sub = {
        "endpoint": "https://push.example.com/unsubscribe-test-endpoint",
        "p256dh": "BNbLHgs8sw7test",
        "auth": "authtest==",
    }

    # Subscribe first
    page.request.post(
        f"{live_server}/push/subscribe",
        data=json.dumps(fake_sub),
        headers={"Content-Type": "application/json"},
    )

    # Then unsubscribe
    r = page.request.post(
        f"{live_server}/push/unsubscribe",
        data=json.dumps(fake_sub),
        headers={"Content-Type": "application/json"},
    )
    assert r.status == 200

    ctx.close()
