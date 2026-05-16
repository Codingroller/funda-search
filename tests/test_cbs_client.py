"""Tests for the CBS OGC API client and its SQLite persistence."""
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db import AsyncSessionLocal, Base, engine
from app.models import CbsBuurt, CbsWijk


@pytest.fixture(autouse=True)
async def _db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


_BUURT_PROPS = {
    "buurtcode": "BU03920301",
    "buurtnaam": "Garenkokerskwartier",
    "wijkcode": "WK039203",
    "gemeentecode": "GM0392",
    "aantal_inwoners": 1985,
    "aantal_huishoudens": 970,
    "bevolkingsdichtheid_inwoners_per_km2": 10451,
    "gemiddelde_huishoudsgrootte": 2.0,
    "oppervlakte_land_in_ha": 19,
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
    "percentage_met_herkomstland_buiten_europa": 15,
}

_WIJK_PROPS = {
    "wijkcode": "WK039203",
    "wijknaam": "Zijlwegkwartier",
    "gemeentecode": "GM0392",
    "aantal_inwoners": 8180,
    "aantal_huishoudens": 4105,
    "bevolkingsdichtheid_inwoners_per_km2": 12654,
    "gemiddelde_huishoudsgrootte": 2.0,
    "oppervlakte_land_in_ha": 65,
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
    "percentage_met_herkomstland_buiten_europa": 15,
}


def _make_geojson_response(props: dict, buurtcode_key: str = "buurtcode") -> dict:
    return {
        "features": [{
            "type": "Feature",
            "bbox": [4.625, 52.384, 4.635, 52.392],
            "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [[[4.625, 52.384], [4.635, 52.384], [4.635, 52.392], [4.625, 52.392], [4.625, 52.384]]]},
        }]
    }


def _mock_httpx(buurt_resp, wijk_resp):
    """Return an httpx.AsyncClient mock that returns different responses per URL."""
    async def _get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "/collections/buurten/" in url:
            resp.json.return_value = buurt_resp
        else:
            resp.json.return_value = wijk_resp
        return resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = _get
    return mock_client


async def test_fetches_buurt_and_wijk_from_pdok():
    from app.cbs_client import get_neighbourhood_stats

    buurt_resp = _make_geojson_response(_BUURT_PROPS)
    wijk_resp = _make_geojson_response(_WIJK_PROPS)

    with patch("app.cbs_client.httpx.AsyncClient", return_value=_mock_httpx(buurt_resp, wijk_resp)):
        result = await get_neighbourhood_stats(52.387, 4.629)

    assert result is not None
    assert result["buurt"]["buurtnaam"] == "Garenkokerskwartier"
    assert result["wijk"]["wijknaam"] == "Zijlwegkwartier"
    assert result["buurt"]["aantal_inwoners"] == 1985


async def test_persists_to_db_after_fetch():
    from app.cbs_client import get_neighbourhood_stats

    buurt_resp = _make_geojson_response(_BUURT_PROPS)
    wijk_resp = _make_geojson_response(_WIJK_PROPS)

    with patch("app.cbs_client.httpx.AsyncClient", return_value=_mock_httpx(buurt_resp, wijk_resp)):
        await get_neighbourhood_stats(52.387, 4.629)

    async with AsyncSessionLocal() as db:
        row = await db.get(CbsBuurt, "BU03920301")
        assert row is not None
        assert row.buurtnaam == "Garenkokerskwartier"
        assert row.wijkcode == "WK039203"

        wrow = await db.get(CbsWijk, "WK039203")
        assert wrow is not None
        assert wrow.wijknaam == "Zijlwegkwartier"


async def test_second_call_uses_db_cache(monkeypatch):
    """After first fetch, a second call with same coords must not hit PDOK."""
    from app.cbs_client import get_neighbourhood_stats

    buurt_resp = _make_geojson_response(_BUURT_PROPS)
    wijk_resp = _make_geojson_response(_WIJK_PROPS)

    call_count = 0

    async def _get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = buurt_resp if "/collections/buurten/" in url else wijk_resp
        return resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = _get

    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock_client):
        await get_neighbourhood_stats(52.387, 4.629)
        first_calls = call_count

    # Second call: should serve from DB without hitting PDOK at all
    result = await get_neighbourhood_stats(52.387, 4.629)
    assert call_count == first_calls, "Second call should hit DB cache, not PDOK"

    assert result is not None
    assert result["buurt"]["buurtnaam"] == "Garenkokerskwartier"


async def test_returns_none_when_pdok_fails():
    from app.cbs_client import get_neighbourhood_stats

    async def _bad_get(url, **kwargs):
        raise Exception("network error")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = _bad_get

    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock_client):
        result = await get_neighbourhood_stats(52.0, 4.0)

    assert result is None


async def test_sentinel_filter_in_template_env():
    from app.templates_env import _cbs_value
    assert _cbs_value(-99997) == "—"
    assert _cbs_value(None) == "—"
    assert _cbs_value(1985) == 1985
    assert _cbs_value(42) == 42
