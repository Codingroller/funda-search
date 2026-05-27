"""Unit tests for app/funda_client.py.

Covers _listing_to_dict, _listing_detail_to_dict, _sync_search,
get_listing_detail (DB cache hit/miss), and _fmt_price_per_m2.
All pyfunda and image-cache calls are mocked — no network I/O.
"""
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from app.time_utils import now_utc

import pytest

from app.funda_client import (
    _fmt_price_per_m2,
    _listing_detail_to_dict,
    _listing_to_dict,
    _sync_search,
)
from app.db import AsyncSessionLocal, Base, engine
from app.models import ListingCache


# ---------------------------------------------------------------------------
# DB fixture (needed for get_listing_detail cache tests)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def _db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ---------------------------------------------------------------------------
# Mock factories
# ---------------------------------------------------------------------------

def _make_search_listing(
    global_id="123",
    price_formatted="€ 450.000 k.k.",
    price_amount=450_000,
    city="Amsterdam",
    title="Keizersgracht 1",
    living_area=85,
    rooms_count=4,
    bedrooms=2,
    energy_label="A",
    photo_urls=None,
    photo_ids=None,
    pub_date=None,
    url="https://funda.nl/1",
):
    """Minimal mock that _listing_to_dict can consume."""
    m = MagicMock()
    m.global_id = global_id
    m.url = url
    m.title = title
    m.city = city
    m.living_area = living_area
    m.rooms_count = rooms_count
    m.bedrooms = bedrooms
    m.energy_label = energy_label
    m.publication_date = pub_date
    m.price = MagicMock(formatted=price_formatted, amount=price_amount)
    m.media = MagicMock(
        photo_urls=photo_urls if photo_urls is not None else ("https://img.funda.nl/p.jpg",),
        photo_ids=photo_ids if photo_ids is not None else (),
    )
    return m


def _make_detail_listing(
    global_id=12345678,
    lat=52.38,
    lon=4.63,
    price_amount=500_000,
    living_area=80,
    photo_urls=("https://cloud.funda.nl/photo1_klein.jpg",),
    with_broker=True,
    with_location=True,
):
    """Richer mock for _listing_detail_to_dict."""
    addr = MagicMock(
        street_name="Teststraat",
        house_number="1",
        house_number_suffix=None,
        postcode="1234 AB",
        city="Amsterdam",
        municipality="Amsterdam",
        neighbourhood="Centrum",
        neighbourhood_identifier=None,
        title="Teststraat 1",
    )
    loc = MagicMock(latitude=lat, longitude=lon) if with_location else None
    pd_mock = MagicMock(
        object_type="Apartment",
        house_type=None,
        construction_year=1930,
        energy_label="B",
    )
    photos = tuple(MagicMock(url=u) for u in photo_urls)
    media = MagicMock(photos=photos)
    if with_broker:
        broker = MagicMock()
        broker.name = "Test Makelaar"
        broker.association = "NVM"
        broker.relative_url = None
    else:
        broker = None

    m = MagicMock()
    m.global_id = global_id
    m.address = addr
    m.location = loc
    m.price = MagicMock(amount=price_amount, formatted=f"€ {price_amount:,}", is_auction=False)
    m.property_details = pd_mock
    m.media = media
    m.broker = broker
    m.rooms_count = 3
    m.bedrooms = 2
    m.living_area = living_area
    m.plot_area = None
    m.energy_label = "B"
    m.status = None
    m.labels = ()
    m.description = "Mooie woning."
    m.description_title = "Omschrijving"
    m.publication_date = "2025-01-01"
    m.url = f"https://funda.nl/koop/amsterdam/{global_id}/"
    m.title = "Teststraat 1"
    m.city = "Amsterdam"
    m.postcode = "1234 AB"
    return m


# ---------------------------------------------------------------------------
# _fmt_price_per_m2
# ---------------------------------------------------------------------------

class TestFmtPricePerM2:
    def test_basic(self):
        assert _fmt_price_per_m2(400_000, 80) == "€ 5.000 / m²"

    def test_no_amount(self):
        assert _fmt_price_per_m2(None, 80) is None

    def test_no_area(self):
        assert _fmt_price_per_m2(400_000, None) is None

    def test_zero_area(self):
        assert _fmt_price_per_m2(400_000, 0) is None

    def test_uses_period_as_thousands_separator(self):
        result = _fmt_price_per_m2(1_000_000, 100)
        assert "." in result
        assert "," not in result


# ---------------------------------------------------------------------------
# _listing_to_dict
# ---------------------------------------------------------------------------

class TestListingToDict:
    def test_basic_fields(self):
        d = _listing_to_dict(_make_search_listing())
        assert d["global_id"] == "123"
        assert d["city"] == "Amsterdam"
        assert d["price"] == "€ 450.000 k.k."
        assert d["living_area"] == 85
        assert d["rooms_count"] == 4
        assert d["energy_label"] == "A"
        assert d["photo_url"] == "https://img.funda.nl/p.jpg"

    def test_global_id_coerced_to_string(self):
        d = _listing_to_dict(_make_search_listing(global_id=99999))
        assert isinstance(d["global_id"], str)
        assert d["global_id"] == "99999"

    def test_no_photo_urls_returns_none(self):
        d = _listing_to_dict(_make_search_listing(photo_urls=[]))
        assert d["photo_url"] is None

    def test_photo_id_fallback_constructs_cdn_url(self):
        # iter_search populates photo_ids but not photo_urls
        d = _listing_to_dict(_make_search_listing(photo_urls=[], photo_ids=("228898333",)))
        assert d["photo_url"] == "https://cloud.funda.nl/valentina_media/228/898/333_groot.jpg"

    def test_short_photo_id_no_path_split(self):
        # IDs shorter than 9 chars are used as-is
        d = _listing_to_dict(_make_search_listing(photo_urls=[], photo_ids=("12345",)))
        assert "12345_groot.jpg" in d["photo_url"]

    def test_no_price_object(self):
        m = _make_search_listing()
        m.price = None
        d = _listing_to_dict(m)
        assert d["price"] is None
        assert d["price_per_m2"] is None

    def test_publication_date_string_passthrough(self):
        d = _listing_to_dict(_make_search_listing(pub_date="2025-03-15"))
        assert d["publication_date"] == "2025-03-15"

    def test_publication_date_datetime_converted(self):
        from datetime import date
        d = _listing_to_dict(_make_search_listing(pub_date=date(2025, 3, 15)))
        assert d["publication_date"] == "2025-03-15"

    def test_price_per_m2_calculated(self):
        d = _listing_to_dict(_make_search_listing(price_amount=400_000, living_area=80))
        assert d["price_per_m2"] == "€ 5.000 / m²"


# ---------------------------------------------------------------------------
# _sync_search
# ---------------------------------------------------------------------------

class TestSyncSearch:
    def test_returns_list_of_dicts(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.iter_search.return_value = [_make_search_listing(global_id="A1")]

        with patch("app.funda_client.Funda", return_value=mock_client):
            result = _sync_search({"location": ["amsterdam"], "category": "buy", "sort": "newest"})

        assert isinstance(result, list)
        assert result[0]["global_id"] == "A1"

    def test_passes_params_to_iter_search(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.iter_search.return_value = []

        with patch("app.funda_client.Funda", return_value=mock_client):
            _sync_search({"location": ["amsterdam"], "category": "buy", "sort": "newest"})

        kw = mock_client.iter_search.call_args.kwargs
        assert kw["location"] == ["amsterdam"]
        assert kw["category"] == "buy"
        assert "max_pages" not in kw

    def test_passes_params_through(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.iter_search.return_value = []

        params = {"location": ["haarlem"], "category": "rent", "sort": "newest", "min_price": 1000}
        with patch("app.funda_client.Funda", return_value=mock_client):
            _sync_search(params)

        kw = mock_client.iter_search.call_args.kwargs
        assert kw["location"] == ["haarlem"]
        assert kw["min_price"] == 1000


# ---------------------------------------------------------------------------
# _listing_detail_to_dict
# ---------------------------------------------------------------------------

class TestListingDetailToDict:
    def _call(self, listing, hero_result="/img/hero.jpg", thumb_result="/img/thumb.jpg"):
        with patch("app.funda_client.cache_hero_sync", return_value=hero_result), \
             patch("app.funda_client.cache_photo_sync", return_value=thumb_result):
            return _listing_detail_to_dict(listing)

    def test_basic_shape(self):
        d = self._call(_make_detail_listing())
        assert d["global_id"] == "12345678"
        assert d["city"] == "Amsterdam"
        assert d["street"] == "Teststraat"
        assert d["postcode"] == "1234 AB"
        assert d["construction_year"] == 1930
        assert d["description"] == "Mooie woning."

    def test_lat_lon_extracted(self):
        d = self._call(_make_detail_listing(lat=52.38, lon=4.63))
        assert d["lat"] == 52.38
        assert d["lon"] == 4.63

    def test_lat_lon_none_when_no_location(self):
        d = self._call(_make_detail_listing(with_location=False))
        assert d["lat"] is None
        assert d["lon"] is None

    def test_first_photo_uses_hero_cache(self):
        called_with = {}

        def _hero(url):
            called_with["hero"] = url
            return "/img/hero.jpg"

        def _thumb(url):
            called_with["thumb"] = url
            return "/img/thumb.jpg"

        listing = _make_detail_listing(
            photo_urls=("https://cdn/a_klein.jpg", "https://cdn/b_klein.jpg")
        )
        with patch("app.funda_client.cache_hero_sync", side_effect=_hero), \
             patch("app.funda_client.cache_photo_sync", side_effect=_thumb):
            d = _listing_detail_to_dict(listing)

        assert called_with["hero"] == "https://cdn/a_klein.jpg"
        assert called_with["thumb"] == "https://cdn/b_klein.jpg"
        assert d["photos"][0] == "/img/hero.jpg"
        assert d["photos"][1] == "/img/thumb.jpg"

    def test_photo_falls_back_to_cdn_url_on_cache_failure(self):
        listing = _make_detail_listing(photo_urls=("https://cdn/x.jpg",))
        with patch("app.funda_client.cache_hero_sync", return_value=None), \
             patch("app.funda_client.cache_photo_sync", return_value=None):
            d = _listing_detail_to_dict(listing)
        assert d["photos"] == ["https://cdn/x.jpg"]

    def test_broker_extracted(self):
        d = self._call(_make_detail_listing(with_broker=True))
        assert d["broker"]["name"] == "Test Makelaar"
        assert d["broker"]["association"] == "NVM"

    def test_no_broker_returns_none(self):
        d = self._call(_make_detail_listing(with_broker=False))
        assert d["broker"] is None

    def test_global_id_is_string(self):
        d = self._call(_make_detail_listing(global_id=99887766))
        assert isinstance(d["global_id"], str)
        assert d["global_id"] == "99887766"

    def test_price_per_m2_calculated(self):
        d = self._call(_make_detail_listing(price_amount=400_000, living_area=80))
        assert d["price_per_m2"] == "€ 5.000 / m²"


# ---------------------------------------------------------------------------
# get_listing_detail — DB cache behaviour
# ---------------------------------------------------------------------------

class TestGetListingDetail:
    async def test_cache_miss_calls_funda(self):
        from app.funda_client import get_listing_detail

        mock_pyfunda = MagicMock()
        mock_pyfunda.__enter__ = MagicMock(return_value=mock_pyfunda)
        mock_pyfunda.__exit__ = MagicMock(return_value=False)
        mock_pyfunda.listing.return_value = _make_detail_listing(global_id=11110001)

        with patch("app.funda_client.Funda", return_value=mock_pyfunda), \
             patch("app.funda_client.cache_hero_sync", return_value=None), \
             patch("app.funda_client.cache_photo_sync", return_value=None):
            result = await get_listing_detail("11110001")

        mock_pyfunda.listing.assert_called_once_with(11110001)
        assert result["global_id"] == "11110001"

    async def test_cache_miss_saves_to_db(self):
        from app.funda_client import get_listing_detail

        mock_pyfunda = MagicMock()
        mock_pyfunda.__enter__ = MagicMock(return_value=mock_pyfunda)
        mock_pyfunda.__exit__ = MagicMock(return_value=False)
        mock_pyfunda.listing.return_value = _make_detail_listing(global_id=11110002)

        with patch("app.funda_client.Funda", return_value=mock_pyfunda), \
             patch("app.funda_client.cache_hero_sync", return_value=None), \
             patch("app.funda_client.cache_photo_sync", return_value=None):
            await get_listing_detail("11110002")

        async with AsyncSessionLocal() as db:
            row = await db.get(ListingCache, "11110002")
        assert row is not None
        payload = json.loads(row.payload_json)
        assert payload["global_id"] == "11110002"

    async def test_cache_hit_skips_funda(self):
        from app.funda_client import get_listing_detail

        async with AsyncSessionLocal() as db:
            db.add(ListingCache(
                global_id="11110003",
                payload_json=json.dumps({"global_id": "11110003", "city": "Cached"}),
                fetched_at=now_utc(),
            ))
            await db.commit()

        with patch("app.funda_client.Funda") as mock_funda_cls:
            result = await get_listing_detail("11110003")

        mock_funda_cls.assert_not_called()
        assert result["city"] == "Cached"

    async def test_stale_cache_refetches(self):
        from app.funda_client import get_listing_detail

        async with AsyncSessionLocal() as db:
            db.add(ListingCache(
                global_id="11110004",
                payload_json=json.dumps({"global_id": "11110004", "city": "OldData"}),
                fetched_at=now_utc() - timedelta(hours=25),
            ))
            await db.commit()

        mock_pyfunda = MagicMock()
        mock_pyfunda.__enter__ = MagicMock(return_value=mock_pyfunda)
        mock_pyfunda.__exit__ = MagicMock(return_value=False)
        mock_pyfunda.listing.return_value = _make_detail_listing(global_id=11110004)

        with patch("app.funda_client.Funda", return_value=mock_pyfunda), \
             patch("app.funda_client.cache_hero_sync", return_value=None), \
             patch("app.funda_client.cache_photo_sync", return_value=None):
            result = await get_listing_detail("11110004")

        mock_pyfunda.listing.assert_called_once()
        assert result["city"] == "Amsterdam"  # fresh data, not "OldData"
