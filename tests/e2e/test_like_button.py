"""Playwright test to diagnose/verify like button size stability on detail page."""
import json
import sqlite3
import time

import pytest

pytestmark = pytest.mark.e2e

_FAKE_GLOBAL_ID = "e2e-test-listing-001"
_FAKE_PAYLOAD = json.dumps({
    "global_id": _FAKE_GLOBAL_ID,
    "title": "Test Listing Amsterdam",
    "price": "€ 400.000",
    "price_amount": 400000,
    "living_area": 100,
    "rooms_count": 4,
    "city": "Amsterdam",
    "postcode": "1011AB",
    "energy_label": "B",
    "construction_year": 2000,
    "object_type": "house",
    "photos": [],
    "url": "https://www.funda.nl/koop/amsterdam/huis-test/",
    "labels": [],
    "plot_area": 0,
    "is_auction": False,
})


def _seed(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT OR IGNORE INTO listing_cache (global_id, payload_json, fetched_at) "
        "VALUES (?, ?, datetime('now'))",
        (_FAKE_GLOBAL_ID, _FAKE_PAYLOAD),
    )
    con.commit()
    con.close()


def test_like_button_size_stable_on_detail_page(live_server, browser):
    _seed(live_server.db_path)

    ctx = browser.new_context(service_workers="block")
    page = ctx.new_page()

    page.goto(f"{live_server}/login")
    page.fill('input[name="username"]', "e2eadmin")
    page.fill('input[name="password"]', "e2etestpass")
    with page.expect_navigation(timeout=10000):
        page.click('button[type="submit"]')

    page.goto(f"{live_server}/listings/{_FAKE_GLOBAL_ID}")
    page.wait_for_load_state("networkidle")

    heart = page.locator(".like-btn-lg")
    assert heart.count() > 0, "Heart button (.like-btn-lg) not found on detail page"

    before = heart.bounding_box()
    page.screenshot(path="/tmp/heart_before.png")
    print(f"\nBefore click: {before}")

    heart.click()
    page.wait_for_timeout(600)
    page.screenshot(path="/tmp/heart_after.png")

    heart_after = page.locator(".like-btn-lg")
    after_count = heart_after.count()
    after = heart_after.bounding_box() if after_count > 0 else None

    any_heart = page.locator(".like-btn")
    print(f"After click:  {after}")
    print(f".like-btn-lg present: {after_count > 0}")
    print(f"All .like-btn boxes:  {[any_heart.nth(i).bounding_box() for i in range(any_heart.count())]}")

    assert after_count > 0, (
        ".like-btn-lg class lost after HTMX swap — the response template is "
        "missing the like-btn-lg class, causing the button to shrink"
    )
    assert abs(before["width"] - after["width"]) < 2, \
        f"Width changed after click: {before['width']} → {after['width']}"
    assert abs(before["height"] - after["height"]) < 2, \
        f"Height changed after click: {before['height']} → {after['height']}"

    ctx.close()
