"""Tests for bid_estimator module (orchestration layer) and bid_model energy helpers."""

import pytest
from app.bid_model import _energy_rank


class TestEnergyRank:
    def test_known_labels(self):
        assert _energy_rank("A") < _energy_rank("C") < _energy_rank("G")

    def test_aplus_better_than_a(self):
        assert _energy_rank("A+") < _energy_rank("A")

    def test_unknown_label_returns_neutral(self):
        assert _energy_rank("X") == 5

    def test_none_returns_neutral(self):
        assert _energy_rank(None) == 5
