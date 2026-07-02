"""Unit tests for woz_client: sibling-nid fallback + candidate ranking."""

import pytest

from app import woz_client as wc


def _doc(nid, toev=None, letter=None, hn=20):
    return {
        "nummeraanduiding_id": nid,
        "huisnummer": hn,
        "huisnummertoevoeging": toev,
        "huisletter": letter,
    }


class TestRankNids:
    def test_exact_toevoeging_first(self):
        items = [_doc("n1", toev="1"), _doc("n2", toev="2"), _doc("nH", toev="H")]
        assert _rank(items, "H")[0] == "nH"

    def test_matches_huisletter(self):
        # Amsterdam "-H" is stored as a huisletter, not a toevoeging.
        items = [_doc("n1", toev="1"), _doc("nH", letter="H")]
        assert _rank(items, "H")[0] == "nH"

    def test_case_insensitive(self):
        items = [_doc("n1", toev="1"), _doc("nA", letter="A")]
        assert _rank(items, "a")[0] == "nA"

    def test_no_suffix_preserves_pdok_order(self):
        items = [_doc("n1", toev="1"), _doc("n2", toev="2")]
        assert _rank(items, None) == ["n1", "n2"]

    def test_siblings_follow_exact_match(self):
        items = [_doc("n1", toev="1"), _doc("n2", toev="2"), _doc("nH", toev="H")]
        assert _rank(items, "H") == ["nH", "n1", "n2"]

    def test_dedup_and_cap(self):
        items = [_doc(f"n{i}", toev=str(i)) for i in range(10)] + [_doc("n0", toev="0")]
        out = _rank(items, None)
        assert len(out) == wc._MAX_WOZ_CANDIDATES
        assert len(set(out)) == len(out)


def _rank(items, toev):
    return wc._rank_nids(items, toev)


@pytest.mark.asyncio
class TestFetchWozFallback:
    async def test_falls_back_to_sibling(self, monkeypatch):
        async def fake_resolve(pc, hn, toe):
            return ["nidH", "nid1"]

        tried = []

        async def fake_woz_for_nid(client, nid):
            tried.append(nid)
            if nid == "nidH":
                return None  # the exact unit 404s
            return {"latest_woz_eur": 697000, "latest_peildatum": "2025-01-01", "history": []}

        monkeypatch.setattr(wc, "_resolve_candidate_nids", fake_resolve)
        monkeypatch.setattr(wc, "_woz_for_nid", fake_woz_for_nid)

        res = await wc._fetch_woz("1071JJ", 20, "H")
        assert res["latest_woz_eur"] == 697000
        assert tried == ["nidH", "nid1"]   # exact first, then sibling

    async def test_first_hit_short_circuits(self, monkeypatch):
        async def fake_resolve(pc, hn, toe):
            return ["nidA", "nidB"]

        tried = []

        async def fake_woz_for_nid(client, nid):
            tried.append(nid)
            return {"latest_woz_eur": 500000, "latest_peildatum": "2025-01-01", "history": []}

        monkeypatch.setattr(wc, "_resolve_candidate_nids", fake_resolve)
        monkeypatch.setattr(wc, "_woz_for_nid", fake_woz_for_nid)

        res = await wc._fetch_woz("1071JJ", 20, None)
        assert res["latest_woz_eur"] == 500000
        assert tried == ["nidA"]           # stopped at the first success

    async def test_all_fail_returns_none(self, monkeypatch):
        async def fake_resolve(pc, hn, toe):
            return ["a", "b"]

        async def fake_woz_for_nid(client, nid):
            return None

        monkeypatch.setattr(wc, "_resolve_candidate_nids", fake_resolve)
        monkeypatch.setattr(wc, "_woz_for_nid", fake_woz_for_nid)
        assert await wc._fetch_woz("1071JJ", 20, "H") is None

    async def test_no_candidates_returns_none(self, monkeypatch):
        async def fake_resolve(pc, hn, toe):
            return []

        monkeypatch.setattr(wc, "_resolve_candidate_nids", fake_resolve)
        assert await wc._fetch_woz("x", 1, None) is None
