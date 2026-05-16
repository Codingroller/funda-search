"""Tests for the CBS OData client and its SQLite persistence."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db import AsyncSessionLocal, Base, engine
from app.models import CbsBuurt, CbsWijk


@pytest.fixture(autouse=True)
async def _db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


_BUURT_OBS = [
    {"Measure": "T001036", "Value": 1985.0, "StringValue": None},
    {"Measure": "3000",    "Value": 960.0,  "StringValue": None},
    {"Measure": "4000",    "Value": 1025.0, "StringValue": None},
    {"Measure": "10680",   "Value": 310.0,  "StringValue": None},
    {"Measure": "1050010_2", "Value": 970.0,"StringValue": None},
    {"Measure": "M000100", "Value": 10451.0,"StringValue": None},
    {"Measure": "M001642", "Value": 670.0,  "StringValue": None},
    {"Measure": "1014800", "Value": 76.0,   "StringValue": None},
    {"Measure": "M000200_2","Value": 485.0, "StringValue": None},
    {"Measure": "D000028", "Value": 0.4,    "StringValue": None},
    {"Measure": "GM000C",  "Value": None,   "StringValue": "Haarlem"},
]

_WIJK_OBS = [
    {"Measure": "T001036", "Value": 8180.0, "StringValue": None},
    {"Measure": "3000",    "Value": 4005.0, "StringValue": None},
    {"Measure": "M000100", "Value": 12654.0,"StringValue": None},
    {"Measure": "M001642", "Value": 574.0,  "StringValue": None},
    {"Measure": "GM000C",  "Value": None,   "StringValue": "Haarlem"},
]


def _mock_client(obs_by_collection: dict, names_by_code: dict):
    """Return an AsyncClient mock that dispatches by URL."""
    async def _get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        params = kwargs.get("params", {})
        if "WijkenEnBuurtenCodes" in url:
            filt = params.get("$filter", "")
            code = filt.split("'")[1] if "'" in filt else ""
            resp.json.return_value = {
                "value": [{"Title": names_by_code.get(code, code)}] if code in names_by_code else []
            }
        else:
            filt = params.get("$filter", "")
            code = filt.split("'")[1] if "'" in filt else ""
            resp.json.return_value = {"value": obs_by_collection.get(code, [])}
        return resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = _get
    return mock_client


async def test_fetches_buurt_and_wijk_from_odata():
    from app.cbs_client import get_neighbourhood_stats

    mock = _mock_client(
        {"BU03920301": _BUURT_OBS, "WK039203": _WIJK_OBS},
        {"BU03920301": "Garenkokerskwartier", "WK039203": "Zijlwegkwartier"},
    )
    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock):
        result = await get_neighbourhood_stats("BU03920301")

    assert result is not None
    assert result["buurt"]["name"] == "Garenkokerskwartier"
    assert result["buurt"]["population"]["total"] == 1985.0
    assert result["wijk"]["name"] == "Zijlwegkwartier"
    assert result["wijk"]["population"]["total"] == 8180.0


async def test_derives_wijk_code_from_buurt_code():
    from app.cbs_client import get_neighbourhood_stats

    fetched_codes = []

    async def _get(url, **kwargs):
        params = kwargs.get("params", {})
        filt = params.get("$filter", "")
        if "'" in filt:
            fetched_codes.append(filt.split("'")[1])
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"value": []}
        return resp

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = _get

    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock):
        await get_neighbourhood_stats("BU99990001")  # fresh code, not in cache

    assert "WK999900" in fetched_codes


async def test_persists_to_db_after_fetch():
    from app.cbs_client import get_neighbourhood_stats

    mock = _mock_client(
        {"BU03920301": _BUURT_OBS, "WK039203": _WIJK_OBS},
        {"BU03920301": "Garenkokerskwartier", "WK039203": "Zijlwegkwartier"},
    )
    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock):
        await get_neighbourhood_stats("BU03920301")

    async with AsyncSessionLocal() as db:
        row = await db.get(CbsBuurt, "BU03920301")
        assert row is not None
        assert row.buurtnaam == "Garenkokerskwartier"
        assert row.wijkcode == "WK039203"

        wrow = await db.get(CbsWijk, "WK039203")
        assert wrow is not None
        assert wrow.wijknaam == "Zijlwegkwartier"


async def test_second_call_uses_db_cache():
    from app.cbs_client import get_neighbourhood_stats

    call_count = 0

    async def _counting_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        params = kwargs.get("params", {})
        filt = params.get("$filter", "")
        code = filt.split("'")[1] if "'" in filt else ""
        if "WijkenEnBuurtenCodes" in url:
            resp.json.return_value = {"value": [{"Title": code}]}
        else:
            data = {"BU03920301": _BUURT_OBS, "WK039203": _WIJK_OBS}
            resp.json.return_value = {"value": data.get(code, [])}
        return resp

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = _counting_get

    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock):
        await get_neighbourhood_stats("BU03920301")
        first_calls = call_count

    result = await get_neighbourhood_stats("BU03920301")
    assert call_count == first_calls, "Second call should hit DB cache, not CBS OData"
    assert result is not None


async def test_returns_none_for_bad_identifier():
    from app.cbs_client import get_neighbourhood_stats
    assert await get_neighbourhood_stats("") is None
    assert await get_neighbourhood_stats("GM0392") is None  # must start with BU


async def test_returns_none_when_odata_fails():
    from app.cbs_client import get_neighbourhood_stats

    async def _fail(*a, **kw):
        raise Exception("network error")

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = _fail

    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock):
        assert await get_neighbourhood_stats("BU88880001") is None  # not in cache


async def test_structure_contains_all_themes():
    from app.cbs_client import get_neighbourhood_stats

    mock = _mock_client(
        {"BU03920301": _BUURT_OBS, "WK039203": _WIJK_OBS},
        {"BU03920301": "Test buurt", "WK039203": "Test wijk"},
    )
    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock):
        result = await get_neighbourhood_stats("BU03920301")

    themes = ["population", "marital", "heritage", "households", "housing",
              "housing_type", "energy", "education", "labour", "income",
              "benefits", "care", "businesses", "mobility", "proximity", "area"]
    for theme in themes:
        assert theme in result["buurt"], f"Missing theme: {theme}"
        assert theme in result["wijk"], f"Missing theme in wijk: {theme}"


async def test_format_price_filter():
    from app.templates_env import _format_price
    assert _format_price(670000) == "670.000"
    assert _format_price(1234567) == "1.234.567"
    assert _format_price(398000) == "398.000"


# ---------------------------------------------------------------------------
# get_buurtcode_from_coords
# ---------------------------------------------------------------------------

async def test_get_buurtcode_from_coords_success():
    from app.cbs_client import get_buurtcode_from_coords

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "features": [{"properties": {"buurtcode": "BU03920301"}}]
    }
    mock.get = AsyncMock(return_value=resp)

    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock):
        code = await get_buurtcode_from_coords(52.387, 4.629)

    assert code == "BU03920301"


async def test_get_buurtcode_from_coords_no_features_returns_none():
    from app.cbs_client import get_buurtcode_from_coords

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"features": []}
    mock.get = AsyncMock(return_value=resp)

    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock):
        code = await get_buurtcode_from_coords(0.0, 0.0)

    assert code is None


async def test_get_buurtcode_from_coords_network_error_returns_none():
    from app.cbs_client import get_buurtcode_from_coords

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = AsyncMock(side_effect=Exception("timeout"))

    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock):
        code = await get_buurtcode_from_coords(52.0, 4.0)

    assert code is None


async def test_get_buurtcode_from_coords_includes_point_in_bbox():
    """Verify the bbox query is centred on the given coordinates."""
    from app.cbs_client import get_buurtcode_from_coords

    captured = {}

    async def _get(url, **kwargs):
        captured["bbox"] = kwargs.get("params", {}).get("bbox", "")
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"features": []}
        return resp

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = _get

    with patch("app.cbs_client.httpx.AsyncClient", return_value=mock):
        await get_buurtcode_from_coords(52.0, 5.0)

    parts = [float(x) for x in captured["bbox"].split(",")]
    assert parts[0] < 5.0 < parts[2]   # lon inside bbox
    assert parts[1] < 52.0 < parts[3]  # lat inside bbox
