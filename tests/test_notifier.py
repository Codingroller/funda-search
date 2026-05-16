import pytest
import respx
import httpx

from app.notifier import send_ntfy
from app.config import settings


@pytest.fixture(autouse=True)
def override_settings(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_base_url", "https://ntfy.example.com")
    monkeypatch.setattr(settings, "ntfy_token", "")


@respx.mock
async def test_basic_notification():
    route = respx.post("https://ntfy.example.com/my-topic").mock(return_value=httpx.Response(200))
    await send_ntfy(topic="my-topic", title="Test", message="Hello!")
    assert route.called
    request = route.calls[0].request
    assert request.headers["x-title"] == "Test"
    assert request.content == b"Hello!"


@respx.mock
async def test_click_and_photo_headers():
    route = respx.post("https://ntfy.example.com/t").mock(return_value=httpx.Response(200))
    await send_ntfy(
        topic="t",
        title="New listing",
        message="body",
        click_url="https://funda.nl/1",
        photo_url="https://img.funda.nl/p.jpg",
    )
    request = route.calls[0].request
    assert request.headers["x-click"] == "https://funda.nl/1"
    assert request.headers["x-attach"] == "https://img.funda.nl/p.jpg"


@respx.mock
async def test_bearer_token(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_token", "secret-token")
    route = respx.post("https://ntfy.example.com/t").mock(return_value=httpx.Response(200))
    await send_ntfy(topic="t", title="T", message="M")
    assert route.calls[0].request.headers["authorization"] == "Bearer secret-token"


@respx.mock
async def test_non_ascii_title_is_sanitized():
    route = respx.post("https://ntfy.example.com/t").mock(return_value=httpx.Response(200))
    await send_ntfy(topic="t", title="Query — €525.000 — Almere", message="body")
    sent_title = route.calls[0].request.headers["x-title"]
    assert sent_title.isascii(), f"Header contained non-ASCII: {sent_title!r}"
    assert "EUR" in sent_title
    assert "-" in sent_title


@respx.mock
async def test_raises_on_http_error():
    respx.post("https://ntfy.example.com/t").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await send_ntfy(topic="t", title="T", message="M")
