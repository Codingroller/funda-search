"""Unit tests for bag_client's 3-hop BAG OGC parsing (helpers monkeypatched)."""

import pytest

from app import bag_client


def _canned(monkeypatch, adres, vbo, pand):
    async def fake_first(client, url, params):
        if "/adres/" in url:
            return adres
        if "/verblijfsobject/" in url:
            return vbo
        return None

    async def fake_get(client, url):
        return pand

    monkeypatch.setattr(bag_client, "_first_feature", fake_first)
    monkeypatch.setattr(bag_client, "_get_feature", fake_get)


@pytest.mark.asyncio
class TestFetchBag:
    async def test_single_family_house(self, monkeypatch):
        _canned(
            monkeypatch,
            adres={"properties": {
                "adresseerbaar_object_type": "Verblijfsobject",
                "adresseerbaar_object_identificatie": "VBO1",
            }},
            vbo={"properties": {
                "oppervlakte": 108, "gebruiksdoel": "woonfunctie",
                "pand.href": ["https://api.pdok.nl/.../pand/items/P1"],
            }},
            pand={"properties": {"bouwjaar": 1986, "aantal_verblijfsobjecten": 1}},
        )
        r = await bag_client._fetch_bag("NID1")
        assert r["living_area"] == 108
        assert r["construction_year"] == 1986
        assert r["is_apartment"] is False
        assert r["object_kind"] == "Verblijfsobject"

    async def test_apartment_when_multiple_units(self, monkeypatch):
        _canned(
            monkeypatch,
            adres={"properties": {
                "adresseerbaar_object_type": "Verblijfsobject",
                "adresseerbaar_object_identificatie": "VBO2",
            }},
            vbo={"properties": {
                "oppervlakte": 74, "gebruiksdoel": "woonfunctie",
                "pand.href": ["https://x/pand/P2"],
            }},
            pand={"properties": {"bouwjaar": 2005, "aantal_verblijfsobjecten": 24}},
        )
        r = await bag_client._fetch_bag("NID2")
        assert r["living_area"] == 74
        assert r["is_apartment"] is True

    async def test_houseboat_has_no_floor_area(self, monkeypatch):
        # Ligplaats → not a Verblijfsobject → no oppervlakte to model.
        _canned(
            monkeypatch,
            adres={"properties": {"adresseerbaar_object_type": "Ligplaats"}},
            vbo=None, pand=None,
        )
        r = await bag_client._fetch_bag("NID3")
        assert r["living_area"] is None
        assert r["is_apartment"] is None
        assert r["object_kind"] == "Ligplaats"

    async def test_missing_pand_leaves_year_unknown(self, monkeypatch):
        _canned(
            monkeypatch,
            adres={"properties": {
                "adresseerbaar_object_type": "Verblijfsobject",
                "adresseerbaar_object_identificatie": "VBO4",
            }},
            vbo={"properties": {"oppervlakte": 90, "gebruiksdoel": "woonfunctie"}},  # no pand.href
            pand=None,
        )
        r = await bag_client._fetch_bag("NID4")
        assert r["living_area"] == 90
        assert r["construction_year"] is None
        assert r["is_apartment"] is None   # aantal unknown → imputed downstream

    async def test_unresolvable_address_returns_none(self, monkeypatch):
        async def fake_first(client, url, params):
            return None

        monkeypatch.setattr(bag_client, "_first_feature", fake_first)
        assert await bag_client._fetch_bag("NIDX") is None


class TestHelpers:
    def test_href_list_and_string(self):
        assert bag_client._href(["https://a"]) == "https://a"
        assert bag_client._href("https://b") == "https://b"
        assert bag_client._href([]) is None
        assert bag_client._href(None) is None

    def test_as_int(self):
        assert bag_client._as_int(108) == 108
        assert bag_client._as_int(108.0) == 108
        assert bag_client._as_int(0) is None
        assert bag_client._as_int(None) is None
        assert bag_client._as_int(True) is None
