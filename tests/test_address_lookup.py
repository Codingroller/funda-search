"""Unit tests for app/address_lookup.py — PDOK Locatieserver resolution.

All httpx calls are mocked; no network I/O.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from app.address_lookup import (
    _doc_to_address,
    _parse_centroide_ll,
    lookup_address,
    resolve_free_text,
    suggest_addresses,
)


def _fake_client(json_payload, raises=None):
    """Build a mock httpx.AsyncClient usable as an async context manager."""
    resp = MagicMock()
    resp.json.return_value = json_payload
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    if raises is not None:
        client.get = AsyncMock(side_effect=raises)
    else:
        client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


_DOC = {
    "id": "adr-123",
    "weergavenaam": "Prinsengracht 263, 1016GV Amsterdam",
    "straatnaam": "Prinsengracht",
    "huisnummer": "263",
    "huisnummertoevoeging": None,
    "huisletter": None,
    "postcode": "1016GV",
    "woonplaatsnaam": "Amsterdam",
    "gemeentenaam": "Amsterdam",
    "centroide_ll": "POINT(4.8842 52.3792)",
    "nummeraanduiding_id": "0363200000123456",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestCentroideParsing:
    def test_point_wkt(self):
        assert _parse_centroide_ll("POINT(4.8842 52.3792)") == (52.3792, 4.8842)

    def test_none(self):
        assert _parse_centroide_ll(None) == (None, None)

    def test_garbage(self):
        assert _parse_centroide_ll("not-a-point") == (None, None)


class TestDocToAddress:
    def test_full_mapping(self):
        a = _doc_to_address(_DOC)
        assert a["pdok_id"] == "adr-123"
        assert a["street"] == "Prinsengracht"
        assert a["huisnummer"] == 263
        assert a["suffix"] is None
        assert a["postcode"] == "1016GV"
        assert a["city"] == "Amsterdam"
        assert a["nummeraanduiding_id"] == "0363200000123456"
        assert a["lat"] == 52.3792 and a["lon"] == 4.8842

    def test_suffix_from_huisletter(self):
        doc = {**_DOC, "huisletter": "A"}
        assert _doc_to_address(doc)["suffix"] == "A"

    def test_suffix_toevoeging_wins(self):
        doc = {**_DOC, "huisnummertoevoeging": "2", "huisletter": "A"}
        assert _doc_to_address(doc)["suffix"] == "2"

    def test_bad_huisnummer(self):
        doc = {**_DOC, "huisnummer": "abc"}
        assert _doc_to_address(doc)["huisnummer"] is None


# ---------------------------------------------------------------------------
# Network-backed functions (mocked)
# ---------------------------------------------------------------------------

class TestSuggest:
    async def test_returns_id_label_pairs(self):
        payload = {"response": {"docs": [
            {"id": "a1", "weergavenaam": "Street 1, 1000AA City"},
            {"id": "a2", "weergavenaam": "Street 2, 1000AA City"},
        ]}}
        client = _fake_client(payload)
        with patch("app.address_lookup.httpx.AsyncClient", return_value=client):
            out = await suggest_addresses("street 1")
        assert out == [
            {"id": "a1", "label": "Street 1, 1000AA City"},
            {"id": "a2", "label": "Street 2, 1000AA City"},
        ]
        assert client.get.call_args.kwargs["params"]["fq"] == "type:adres"

    async def test_short_query_skips_request(self):
        client = _fake_client({"response": {"docs": []}})
        with patch("app.address_lookup.httpx.AsyncClient", return_value=client):
            assert await suggest_addresses("a") == []
        client.get.assert_not_called()

    async def test_error_returns_empty(self):
        client = _fake_client(None, raises=RuntimeError("boom"))
        with patch("app.address_lookup.httpx.AsyncClient", return_value=client):
            assert await suggest_addresses("street") == []


class TestLookup:
    async def test_maps_doc(self):
        client = _fake_client({"response": {"docs": [_DOC]}})
        with patch("app.address_lookup.httpx.AsyncClient", return_value=client):
            a = await lookup_address("adr-123")
        assert a["huisnummer"] == 263 and a["lat"] == 52.3792
        assert "nummeraanduiding_id" in client.get.call_args.kwargs["params"]["fl"]

    async def test_no_docs_returns_none(self):
        client = _fake_client({"response": {"docs": []}})
        with patch("app.address_lookup.httpx.AsyncClient", return_value=client):
            assert await lookup_address("adr-123") is None

    async def test_empty_id_returns_none(self):
        assert await lookup_address("") is None


class TestResolveFreeText:
    async def test_maps_top_hit(self):
        client = _fake_client({"response": {"docs": [_DOC]}})
        with patch("app.address_lookup.httpx.AsyncClient", return_value=client):
            a = await resolve_free_text("prinsengracht 263 amsterdam")
        assert a["postcode"] == "1016GV"
        assert client.get.call_args.kwargs["params"]["fq"] == "type:adres"

    async def test_short_query_returns_none(self):
        assert await resolve_free_text("a") is None
