"""
Tests for NO-side forensic diagnostics in ArbitrageEngine.

Covers:
- SideDiagnostics.to_dict() roundtrip
- NoPriceSource classification (REAL_BOOK / SYNTHETIC / MISSING)
- no_best_ask=0.99 → direction_reason = NO_SIDE_UNTRADABLE
- no_best_ask=0.45 with favorable NO edge → NO_EDGE_DOMINATES
- both edges negative → BOTH_EDGES_NEGATIVE
- diagnostics stored in _last_diagnostics after _evaluate_market
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from strategies.arbitrage_engine import (
    ArbitrageEngine,
    NoPriceSource,
    NoSideStatus,
    SideDiagnostics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _future_end_date(minutes: int = 60) -> str:
    """Return an ISO-8601 end date string that is `minutes` from now."""
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_market(
    condition_id: str = "cond_abc",
    question: str = "Bitcoin Up or Down - March 16, 7:00PM-8:00PM ET",
    best_ask: str = "0.55",
    best_bid: str = "0.53",
    no_best_ask=None,
    no_best_bid=None,
    yes_token_id: str = "yes_tok_1",
    no_token_id: str = "no_tok_1",
    end_date: str | None = None,
) -> dict:
    return {
        "condition_id": condition_id,
        "question": question,
        "best_ask": best_ask,
        "best_bid": best_bid,
        "no_best_ask": no_best_ask,
        "no_best_bid": no_best_bid,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "endDate": end_date or _future_end_date(60),
    }


def _make_engine() -> ArbitrageEngine:
    """Create an ArbitrageEngine with no external dependencies."""
    return ArbitrageEngine(http_session=None, binance_feed=None, smart_trader_tracker=None)


# ---------------------------------------------------------------------------
# 1. SideDiagnostics.to_dict() roundtrip
# ---------------------------------------------------------------------------

class TestSideDiagnosticsToDict:
    def test_default_values_roundtrip(self):
        diag = SideDiagnostics()
        d = diag.to_dict()
        assert d["market_id"] == ""
        assert d["market_title"] == ""
        assert d["yes_edge"] == 0.0
        assert d["no_edge"] == 0.0
        assert d["no_price_source"] == "MISSING"
        assert d["no_token_id_present"] is False
        assert d["no_book_fetched"] is False
        assert d["selected_direction"] == "NONE"
        assert d["direction_reason"] == ""

    def test_populated_values_roundtrip(self):
        diag = SideDiagnostics(
            market_id="mkt_001",
            market_title="Bitcoin Up or Down",
            asset="BTCUSDT",
            horizon="1h",
            timestamp_utc="2026-03-16T12:00:00+00:00",
            yes_best_bid=0.53,
            yes_best_ask=0.55,
            yes_edge=0.05,
            bayesian_prob=0.60,
            no_best_bid=0.42,
            no_best_ask=0.45,
            no_edge=0.08,
            no_prob=0.40,
            no_price_source=NoPriceSource.REAL_BOOK.value,
            no_token_id_present=True,
            no_book_fetched=True,
            selected_direction="NO",
            direction_reason=NoSideStatus.NO_EDGE_DOMINATES.value,
        )
        d = diag.to_dict()

        assert d["market_id"] == "mkt_001"
        assert d["asset"] == "BTCUSDT"
        assert d["horizon"] == "1h"
        assert d["yes_edge"] == pytest.approx(0.05, abs=1e-6)
        assert d["bayesian_prob"] == pytest.approx(0.60, abs=1e-6)
        assert d["no_best_ask"] == pytest.approx(0.45, abs=1e-4)
        assert d["no_edge"] == pytest.approx(0.08, abs=1e-6)
        assert d["no_price_source"] == NoPriceSource.REAL_BOOK.value
        assert d["no_token_id_present"] is True
        assert d["no_book_fetched"] is True
        assert d["selected_direction"] == "NO"
        assert d["direction_reason"] == NoSideStatus.NO_EDGE_DOMINATES.value

    def test_market_title_truncated_to_60_chars(self):
        long_title = "A" * 80
        diag = SideDiagnostics(market_title=long_title)
        d = diag.to_dict()
        assert len(d["market_title"]) == 60

    def test_edges_are_rounded(self):
        diag = SideDiagnostics(yes_edge=0.123456789, no_edge=0.987654321)
        d = diag.to_dict()
        # to_dict rounds to 6 decimal places
        assert d["yes_edge"] == pytest.approx(0.123457, abs=1e-6)
        assert d["no_edge"] == pytest.approx(0.987654, abs=1e-6)

    def test_no_price_source_values_are_strings(self):
        for src in NoPriceSource:
            diag = SideDiagnostics(no_price_source=src.value)
            assert diag.to_dict()["no_price_source"] == src.value


# ---------------------------------------------------------------------------
# 2. NoPriceSource classification
# ---------------------------------------------------------------------------

class TestNoPriceSourceClassification:
    """
    Verify that _evaluate_market assigns the correct NoPriceSource based on
    whether no_best_ask is present in the market dict.
    """

    def _run(self, market: dict) -> SideDiagnostics | None:
        engine = _make_engine()
        asyncio.run(engine._evaluate_market(market, capital=500.0, z_score=0.0, signal_type="bayesian"))
        diags = engine.get_last_diagnostics()
        return diags.get(market["condition_id"])

    def test_no_best_ask_present_is_real_book(self):
        market = _make_market(best_ask="0.55", best_bid="0.53", no_best_ask="0.45", no_best_bid="0.43")
        diag = self._run(market)
        assert diag is not None
        assert diag.no_price_source == NoPriceSource.REAL_BOOK.value

    def test_no_best_ask_absent_is_synthetic(self):
        market = _make_market(best_ask="0.55", best_bid="0.53", no_best_ask=None, no_best_bid=None)
        diag = self._run(market)
        assert diag is not None
        assert diag.no_price_source == NoPriceSource.SYNTHETIC.value

    def test_no_book_fetched_flag_matches_presence(self):
        """no_book_fetched should be True only when no_best_ask is in the market dict."""
        market_with = _make_market(no_best_ask="0.45")
        market_without = _make_market(no_best_ask=None)

        engine = _make_engine()
        asyncio.run(engine._evaluate_market(market_with, 500.0, 0.0, "bayesian"))
        asyncio.run(engine._evaluate_market(market_without, 500.0, 0.0, "bayesian"))
        diags = engine.get_last_diagnostics()

        # Both share the same condition_id; use separate engines
        engine_w = _make_engine()
        engine_wo = _make_engine()
        asyncio.run(engine_w._evaluate_market(market_with, 500.0, 0.0, "bayesian"))
        asyncio.run(engine_wo._evaluate_market(market_without, 500.0, 0.0, "bayesian"))

        diag_w = engine_w.get_last_diagnostics().get(market_with["condition_id"])
        diag_wo = engine_wo.get_last_diagnostics().get(market_without["condition_id"])

        assert diag_w is not None and diag_w.no_book_fetched is True
        assert diag_wo is not None and diag_wo.no_book_fetched is False

    def test_no_token_id_present_flag(self):
        market_with_token = _make_market(no_token_id="no_tok_xyz", no_best_ask="0.45")
        market_without_token = _make_market(no_token_id="", no_best_ask="0.45")
        market_without_token["condition_id"] = "cond_no_tok"

        engine_w = _make_engine()
        engine_wo = _make_engine()
        asyncio.run(engine_w._evaluate_market(market_with_token, 500.0, 0.0, "bayesian"))
        asyncio.run(engine_wo._evaluate_market(market_without_token, 500.0, 0.0, "bayesian"))

        diag_w = engine_w.get_last_diagnostics().get(market_with_token["condition_id"])
        diag_wo = engine_wo.get_last_diagnostics().get(market_without_token["condition_id"])

        assert diag_w is not None and diag_w.no_token_id_present is True
        assert diag_wo is not None and diag_wo.no_token_id_present is False


# ---------------------------------------------------------------------------
# 3. no_best_ask=0.99 → NO_SIDE_UNTRADABLE
# ---------------------------------------------------------------------------

class TestNoSideUntradable:
    """
    When a real NO book is present but the ask is >= 0.95, the NO side is
    classified as UNTRADABLE.  The engine should record NO_SIDE_UNTRADABLE in
    direction_reason (if no_edge > yes_edge) and not select the NO side.
    """

    def test_no_ask_099_is_untradable(self):
        # yes_price = 0.55, bayesian_prob ≈ 0.55 (neutral inputs) → yes_edge ≈ 0
        # no_prob ≈ 0.45, no_ask = 0.99 → no_edge ≈ -0.54  (very negative)
        # Even though no_edge < yes_edge, the health classification should be UNTRADABLE
        # and direction_reason should reflect NO_SIDE_UNTRADABLE only when no_edge > yes_edge.
        # With no_ask=0.99, no_edge is deeply negative, so direction_reason will be
        # BOTH_EDGES_NEGATIVE or YES_EDGE_DOMINATES depending on yes_edge sign.
        # The key assertion: no_price_source == REAL_BOOK and no_ask stored correctly.
        market = _make_market(
            best_ask="0.55",
            best_bid="0.53",
            no_best_ask="0.99",
            no_best_bid="0.01",
        )
        engine = _make_engine()
        asyncio.run(engine._evaluate_market(market, 500.0, 0.0, "bayesian"))
        diag = engine.get_last_diagnostics().get(market["condition_id"])

        assert diag is not None
        assert diag.no_price_source == NoPriceSource.REAL_BOOK.value
        assert diag.no_best_ask == pytest.approx(0.99, abs=1e-4)

    def test_no_ask_099_direction_reason_untradable_when_no_edge_dominates(self):
        """
        For the UNTRADABLE branch to fire, no_edge must be > 0 AND > yes_edge.
        With no_ask=0.99 that requires no_prob > 0.99, i.e. bayesian_prob < 0.01.
        We use the mock to inject bayesian_prob=0.005 directly.

        Market: yes_price=0.55, bayesian_prob=0.005 (mocked)
          yes_edge = 0.005 - 0.55 = -0.545  (negative)
          no_prob  = 0.995
          no_edge  = 0.995 - 0.99 = 0.005   (positive, > yes_edge)
          → UNTRADABLE branch fires → NO_SIDE_UNTRADABLE
        """
        from strategies.bayesian import BayesianEstimate

        market = _make_market(
            best_ask="0.55",
            best_bid="0.53",
            no_best_ask="0.99",
            no_best_bid="0.01",
        )
        engine = _make_engine()

        mock_estimate = BayesianEstimate(
            probability=0.005, prior=0.55, signal_strength=0.0, direction="DOWN"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            asyncio.run(engine._evaluate_market(market, 500.0, 0.0, "bayesian"))

        diag = engine.get_last_diagnostics().get(market["condition_id"])
        assert diag is not None
        assert diag.no_price_source == NoPriceSource.REAL_BOOK.value
        assert diag.direction_reason == NoSideStatus.NO_SIDE_UNTRADABLE.value
        # The NO side must NOT be selected (real-book health = UNTRADABLE)
        assert diag.selected_direction == "NONE"

    def test_no_ask_090_is_suspicious(self):
        """
        no_best_ask=0.90 with no_edge > 0 and no_edge > yes_edge → NO_SIDE_BOOK_SUSPICIOUS.

        Market: yes_price=0.55, bayesian_prob=0.07 (mocked)
          yes_edge = 0.07 - 0.55 = -0.48  (negative)
          no_prob  = 0.93
          no_edge  = 0.93 - 0.90 = 0.03   (positive, > yes_edge)
          0.90 >= _NO_SUSPICIOUS_THRESHOLD (0.90) but < _NO_UNTRADABLE_THRESHOLD (0.95)
          → SUSPICIOUS branch fires → NO_SIDE_BOOK_SUSPICIOUS
        """
        from strategies.bayesian import BayesianEstimate

        market = _make_market(
            best_ask="0.55",
            best_bid="0.53",
            no_best_ask="0.90",
            no_best_bid="0.05",
        )
        engine = _make_engine()
        mock_estimate = BayesianEstimate(
            probability=0.07, prior=0.55, signal_strength=0.0, direction="DOWN"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            asyncio.run(engine._evaluate_market(market, 500.0, 0.0, "bayesian"))

        diag = engine.get_last_diagnostics().get(market["condition_id"])
        assert diag is not None
        assert diag.direction_reason == NoSideStatus.NO_SIDE_BOOK_SUSPICIOUS.value
        assert diag.selected_direction == "NONE"


# ---------------------------------------------------------------------------
# 4. no_best_ask=0.45 with favorable NO edge → NO_EDGE_DOMINATES
# ---------------------------------------------------------------------------

class TestNoEdgeDominates:
    """
    When the real NO book has a healthy ask and no_edge > yes_edge > 0,
    the engine should classify it as NO_EDGE_DOMINATES and (if size > 0)
    select the NO direction.
    """

    def test_healthy_no_book_no_edge_dominates(self):
        """
        yes_price=0.55, bayesian_prob=0.75
          yes_edge = 0.75 - 0.55 = +0.20
          no_prob  = 0.25
          no_price_ask = 0.20  →  no_edge = 0.25 - 0.20 = +0.05

        yes_edge=0.20 > no_edge=0.05  → YES_EDGE_DOMINATES
        We need no_edge > yes_edge.

        Use: yes_price=0.80, bayesian_prob=0.35
          yes_edge = 0.35 - 0.80 = -0.45
          no_prob  = 0.65
          no_price_ask = 0.20  →  no_edge = 0.65 - 0.20 = +0.45 > yes_edge  → NO_EDGE_DOMINATES
        """
        from strategies.bayesian import BayesianEstimate

        market = _make_market(
            best_ask="0.80",
            best_bid="0.78",
            no_best_ask="0.20",
            no_best_bid="0.18",
        )
        engine = _make_engine()

        mock_estimate = BayesianEstimate(
            probability=0.35, prior=0.80, signal_strength=0.3, direction="DOWN"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            asyncio.run(engine._evaluate_market(market, 500.0, 0.0, "bayesian"))

        diag = engine.get_last_diagnostics().get(market["condition_id"])
        assert diag is not None
        assert diag.no_price_source == NoPriceSource.REAL_BOOK.value
        assert diag.direction_reason == NoSideStatus.NO_EDGE_DOMINATES.value

    def test_no_edge_dominates_selects_no_direction(self):
        """
        When NO_EDGE_DOMINATES and book is healthy, _evaluate_market may return a
        TradeSignal with direction=NO (if Kelly size > 0 and edge > min_edge).
        """
        from strategies.bayesian import BayesianEstimate

        # yes_price=0.80 (valid: between 0.05 and 0.95)
        # no_ask=0.20 (healthy, well below 0.90 threshold)
        # bayesian_prob=0.35 → no_prob=0.65 → no_edge=0.45 → big edge
        market = _make_market(
            best_ask="0.80",
            best_bid="0.78",
            no_best_ask="0.20",
            no_best_bid="0.18",
            no_token_id="no_tok_valid",
        )
        engine = _make_engine()

        mock_estimate = BayesianEstimate(
            probability=0.35, prior=0.80, signal_strength=0.3, direction="DOWN"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            result = asyncio.run(
                engine._evaluate_market(market, 500.0, 0.0, "bayesian")
            )

        diag = engine.get_last_diagnostics().get(market["condition_id"])
        assert diag is not None
        assert diag.direction_reason == NoSideStatus.NO_EDGE_DOMINATES.value
        # If a signal is returned, it must be for the NO direction
        if result is not None:
            assert result.direction == "NO"
            assert result.token_id == "no_tok_valid"

    def test_yes_edge_dominates_when_yes_greater(self):
        """
        yes_price=0.40, bayesian_prob=0.70
          yes_edge = 0.30
          no_prob  = 0.30, no_ask = 0.60  → no_edge = -0.30
        yes_edge > no_edge and yes_edge > 0 → YES_EDGE_DOMINATES
        """
        from strategies.bayesian import BayesianEstimate

        market = _make_market(
            best_ask="0.40",
            best_bid="0.38",
            no_best_ask="0.60",
            no_best_bid="0.58",
        )
        engine = _make_engine()

        mock_estimate = BayesianEstimate(
            probability=0.70, prior=0.40, signal_strength=0.5, direction="UP"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            asyncio.run(engine._evaluate_market(market, 500.0, 0.0, "bayesian"))

        diag = engine.get_last_diagnostics().get(market["condition_id"])
        assert diag is not None
        assert diag.direction_reason == NoSideStatus.YES_EDGE_DOMINATES.value


# ---------------------------------------------------------------------------
# 5. Both edges negative → BOTH_EDGES_NEGATIVE
# ---------------------------------------------------------------------------

class TestBothEdgesNegative:
    """
    When bayesian_prob ≈ market_price (no mispricing), both edges will be
    close to zero or negative.  With appropriate setup we can force both
    to be definitively negative.
    """

    def test_both_edges_negative_no_signal(self):
        """
        yes_price=0.55, bayesian_prob=0.40
          yes_edge = 0.40 - 0.55 = -0.15  (negative)
          no_prob  = 0.60, no_ask = 0.80  → no_edge = 0.60 - 0.80 = -0.20  (negative)
        → BOTH_EDGES_NEGATIVE
        """
        from strategies.bayesian import BayesianEstimate

        market = _make_market(
            best_ask="0.55",
            best_bid="0.53",
            no_best_ask="0.80",
            no_best_bid="0.78",
        )
        engine = _make_engine()

        mock_estimate = BayesianEstimate(
            probability=0.40, prior=0.55, signal_strength=0.1, direction="DOWN"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            result = asyncio.run(
                engine._evaluate_market(market, 500.0, 0.0, "bayesian")
            )

        diag = engine.get_last_diagnostics().get(market["condition_id"])
        assert diag is not None
        assert diag.direction_reason == NoSideStatus.BOTH_EDGES_NEGATIVE.value
        assert diag.selected_direction == "NONE"
        assert result is None

    def test_both_edges_negative_with_synthetic_no_price(self):
        """Same scenario but no_best_ask is absent (SYNTHETIC source)."""
        from strategies.bayesian import BayesianEstimate

        # yes_price=0.55, synthetic_no_ask = 1 - 0.55 = 0.45
        # bayesian_prob=0.40 → yes_edge=-0.15, no_prob=0.60 → no_edge=0.60-0.45=+0.15
        # That would be positive no_edge.  Use a probe where both are negative:
        # yes_price=0.55, bayesian_prob=0.40, synthetic_no_ask=0.45
        # no_edge = (1-0.40) - (1-0.55) = 0.60 - 0.45 = 0.15  (not negative!)
        # To get both negative synthetically: yes_price close to bayesian and
        # market is near-fair. Use yes_price=0.50, bayesian_prob=0.50
        # yes_edge = 0.50-0.50=0, synthetic_no_ask=0.50, no_edge=0.50-0.50=0
        # That's == 0, which goes to the else branch (BOTH_EDGES_NEGATIVE).
        market = _make_market(
            best_ask="0.50",
            best_bid="0.48",
            no_best_ask=None,
            no_best_bid=None,
        )
        engine = _make_engine()

        mock_estimate = BayesianEstimate(
            probability=0.50, prior=0.50, signal_strength=0.0, direction="NEUTRAL"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            result = asyncio.run(
                engine._evaluate_market(market, 500.0, 0.0, "bayesian")
            )

        diag = engine.get_last_diagnostics().get(market["condition_id"])
        assert diag is not None
        # yes_edge = 0.50 - 0.50 = 0.0, no_edge = 0.50 - (1-0.50) = 0.0
        # Both <= 0 → BOTH_EDGES_NEGATIVE
        assert diag.direction_reason == NoSideStatus.BOTH_EDGES_NEGATIVE.value
        assert result is None


# ---------------------------------------------------------------------------
# 6. Diagnostics stored in _last_diagnostics after _evaluate_market
# ---------------------------------------------------------------------------

class TestDiagnosticsStorage:
    """
    Every market passed to _evaluate_market must appear in _last_diagnostics,
    regardless of whether a trade signal was produced.
    """

    def test_rejected_market_still_stored(self):
        """A market that produces no signal should still be in _last_diagnostics."""
        from strategies.bayesian import BayesianEstimate

        market = _make_market(
            condition_id="cond_rejected",
            best_ask="0.55",
            best_bid="0.53",
            no_best_ask=None,
        )
        engine = _make_engine()

        # Make bayesian return a value that yields near-zero or negative edges
        mock_estimate = BayesianEstimate(
            probability=0.50, prior=0.55, signal_strength=0.0, direction="NEUTRAL"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            result = asyncio.run(
                engine._evaluate_market(market, 500.0, 0.0, "bayesian")
            )

        diags = engine.get_last_diagnostics()
        assert "cond_rejected" in diags
        assert result is None

    def test_accepted_market_stored_with_direction(self):
        """A market that generates a trade signal should have selected_direction set."""
        from strategies.bayesian import BayesianEstimate

        market = _make_market(
            condition_id="cond_accepted",
            best_ask="0.40",
            best_bid="0.38",
            no_best_ask=None,
            no_best_bid=None,
        )
        engine = _make_engine()

        mock_estimate = BayesianEstimate(
            probability=0.75, prior=0.40, signal_strength=0.6, direction="UP"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            result = asyncio.run(
                engine._evaluate_market(market, 500.0, 0.0, "bayesian")
            )

        diags = engine.get_last_diagnostics()
        assert "cond_accepted" in diags
        diag = diags["cond_accepted"]
        if result is not None:
            assert diag.selected_direction == result.direction
        else:
            assert diag.selected_direction == "NONE"

    def test_multiple_markets_each_get_diagnostics(self):
        """
        Calling _evaluate_market for several markets in sequence accumulates
        all of them in _last_diagnostics (keyed by condition_id).
        """
        from strategies.bayesian import BayesianEstimate

        markets = [
            _make_market(condition_id=f"cond_{i}", no_best_ask=None)
            for i in range(3)
        ]
        engine = _make_engine()

        mock_estimate = BayesianEstimate(
            probability=0.55, prior=0.55, signal_strength=0.1, direction="UP"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            for m in markets:
                asyncio.run(engine._evaluate_market(m, 500.0, 0.0, "bayesian"))

        diags = engine.get_last_diagnostics()
        for i in range(3):
            assert f"cond_{i}" in diags

    def test_diagnostics_cleared_on_analyze(self):
        """
        analyze() clears _last_diagnostics at the start of each run so stale
        entries from a prior cycle do not persist.
        """
        from strategies.bayesian import BayesianEstimate

        market = _make_market(condition_id="cond_stale", no_best_ask=None)
        engine = _make_engine()

        # Plant a stale entry manually
        engine._last_diagnostics["stale_key"] = SideDiagnostics(market_id="stale_key")

        mock_estimate = BayesianEstimate(
            probability=0.55, prior=0.55, signal_strength=0.1, direction="NEUTRAL"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            asyncio.run(engine.analyze([market], capital=500.0))

        diags = engine.get_last_diagnostics()
        assert "stale_key" not in diags

    def test_get_last_diagnostics_returns_copy(self):
        """
        get_last_diagnostics() returns a shallow dict copy via dict().
        Adding or removing keys in the returned dict must not affect the
        engine's internal _last_diagnostics dict.
        """
        engine = _make_engine()
        engine._last_diagnostics["x"] = SideDiagnostics(market_id="x")

        returned = engine.get_last_diagnostics()

        # Mutating the returned dict's keys must not change the internal dict
        returned["new_key"] = SideDiagnostics(market_id="new_key")
        del returned["x"]

        assert "x" in engine._last_diagnostics
        assert "new_key" not in engine._last_diagnostics


# ---------------------------------------------------------------------------
# 7. Edge arithmetic correctness
# ---------------------------------------------------------------------------

class TestEdgeArithmetic:
    """
    Verify that yes_edge and no_edge stored in SideDiagnostics are computed
    correctly from the bayesian probability and market prices.
    """

    def _get_diag(self, bayesian_prob: float, yes_price: float, no_ask: float | None) -> SideDiagnostics:
        from strategies.bayesian import BayesianEstimate

        market = _make_market(
            best_ask=str(yes_price),
            best_bid=str(round(yes_price - 0.02, 4)),
            no_best_ask=str(no_ask) if no_ask is not None else None,
            no_best_bid=str(round(no_ask - 0.02, 4)) if no_ask is not None else None,
        )
        engine = _make_engine()
        mock_estimate = BayesianEstimate(
            probability=bayesian_prob, prior=yes_price, signal_strength=0.2, direction="UP"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            asyncio.run(engine._evaluate_market(market, 500.0, 0.0, "bayesian"))

        return engine.get_last_diagnostics().get(market["condition_id"])

    def test_yes_edge_equals_prob_minus_ask(self):
        diag = self._get_diag(bayesian_prob=0.65, yes_price=0.50, no_ask=None)
        assert diag is not None
        assert diag.yes_edge == pytest.approx(0.65 - 0.50, abs=1e-4)

    def test_no_edge_real_book(self):
        # no_prob = 1 - 0.65 = 0.35; no_ask = 0.30 → no_edge = 0.05
        diag = self._get_diag(bayesian_prob=0.65, yes_price=0.50, no_ask=0.30)
        assert diag is not None
        assert diag.no_edge == pytest.approx(0.35 - 0.30, abs=1e-4)

    def test_no_edge_synthetic(self):
        # Synthetic no_ask = 1 - yes_price = 1 - 0.50 = 0.50
        # no_edge = (1-0.65) - 0.50 = 0.35 - 0.50 = -0.15
        diag = self._get_diag(bayesian_prob=0.65, yes_price=0.50, no_ask=None)
        assert diag is not None
        expected_no_ask = round(1.0 - 0.50, 4)
        assert diag.no_best_ask == pytest.approx(expected_no_ask, abs=1e-4)
        assert diag.no_edge == pytest.approx((1.0 - 0.65) - expected_no_ask, abs=1e-4)

    def test_no_real_book_missing_used_as_reason_when_no_edge_better(self):
        """
        When no real book is present (SYNTHETIC) and no_edge > yes_edge, the engine
        records NO_REAL_BOOK_MISSING as the reason and does not select NO.
        """
        from strategies.bayesian import BayesianEstimate

        # yes_price=0.80, bayesian_prob=0.30
        # yes_edge = 0.30 - 0.80 = -0.50
        # synthetic_no_ask = 1 - 0.80 = 0.20
        # no_edge = 0.70 - 0.20 = 0.50   →  no_edge > yes_edge  → NO_REAL_BOOK_MISSING
        market = _make_market(
            best_ask="0.80",
            best_bid="0.78",
            no_best_ask=None,
            no_best_bid=None,
        )
        engine = _make_engine()
        mock_estimate = BayesianEstimate(
            probability=0.30, prior=0.80, signal_strength=0.3, direction="DOWN"
        )
        with patch.object(engine.bayesian, "estimate", return_value=mock_estimate):
            asyncio.run(engine._evaluate_market(market, 500.0, 0.0, "bayesian"))

        diag = engine.get_last_diagnostics().get(market["condition_id"])
        assert diag is not None
        assert diag.no_price_source == NoPriceSource.SYNTHETIC.value
        assert diag.direction_reason == NoSideStatus.NO_REAL_BOOK_MISSING.value
        assert diag.selected_direction == "NONE"
