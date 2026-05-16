from unittest.mock import MagicMock, patch
import pytest

from app.funda_client import _listing_to_dict, _sync_search


def _make_listing(global_id="123", price_formatted="€ 450.000 k.k.", price_amount=450000,
                  city="Amsterdam", title="Keizersgracht 1", living_area=85,
                  rooms_count=4, bedrooms=2, energy_label="A", photo_urls=None,
                  photo_ids=None, url="https://funda.nl/1"):
    listing = MagicMock()
    listing.global_id = global_id
    listing.url = url
    listing.title = title
    listing.city = city
    listing.living_area = living_area
    listing.rooms_count = rooms_count
    listing.bedrooms = bedrooms
    listing.energy_label = energy_label
    listing.publication_date = None
    listing.price = MagicMock()
    listing.price.formatted = price_formatted
    listing.price.amount = price_amount
    listing.media = MagicMock()
    listing.media.photo_urls = photo_urls if photo_urls is not None else ("https://img.funda.nl/photo1.jpg",)
    listing.media.photo_ids = photo_ids if photo_ids is not None else ()
    return listing


class TestListingToDict:
    def test_basic_fields(self):
        listing = _make_listing()
        d = _listing_to_dict(listing)
        assert d["global_id"] == "123"
        assert d["city"] == "Amsterdam"
        assert d["price"] == "€ 450.000 k.k."
        assert d["living_area"] == 85
        assert d["rooms_count"] == 4
        assert d["energy_label"] == "A"
        assert d["photo_url"] == "https://img.funda.nl/photo1.jpg"

    def test_no_photo(self):
        listing = _make_listing(photo_urls=[])
        d = _listing_to_dict(listing)
        assert d["photo_url"] is None

    def test_no_price(self):
        listing = _make_listing()
        listing.price = None
        d = _listing_to_dict(listing)
        assert d["price"] is None

    def test_global_id_is_string(self):
        listing = _make_listing(global_id=99999)
        d = _listing_to_dict(listing)
        assert isinstance(d["global_id"], str)


class TestSyncSearch:
    def test_returns_list_of_dicts(self):
        mock_listing = _make_listing(global_id="A1")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.iter_search.return_value = [mock_listing]

        with patch("app.funda_client.Funda", return_value=mock_client):
            result = _sync_search({"location": ["amsterdam"], "category": "buy", "sort": "newest"})

        assert isinstance(result, list)
        assert result[0]["global_id"] == "A1"

    def test_caps_at_2_pages(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.iter_search.return_value = []

        with patch("app.funda_client.Funda", return_value=mock_client):
            _sync_search({"location": ["amsterdam"], "category": "buy", "sort": "newest"})

        call_kwargs = mock_client.iter_search.call_args
        assert call_kwargs.kwargs.get("max_pages") == 2
