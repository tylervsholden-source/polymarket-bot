"""
Tests for NO-side execution path in ArbitrageEngine._evaluate_market.

Verifies:
1. direction=YES uses yes_token_id
2. direction=NO (favorable conditions) uses no_token_id
3. NO direction requires REAL_BOOK + no_side_health == "OK"
4. NO with no_best_ask=0.99 is rejected (SUSPICIOUS/UNTRADABLE threshold)
5. NO without no_best_ask (None) falls back to SYNTHETIC, not selected
6. TradeSignal.direction matches side_diagnostics.selected_direction
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from strategies.arbitrage_engine import ArbitrageEngine, NoPriceSource, NoSideStatus


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _base_market(**overrides) -> dict:
    """Return a well-formed market dict; caller can override individual fields."""
    m = {
        "condition_id": "abc123",
        "question": "Bitcoin Up or Down - March 16, 7:10PM-7:15PM ET",
        "best_ask": "0.55",
        "best_bid": "0.53",
        "no_best_ask": "0.45",
        "no_best_bid": "0.43",
        "yes_token_id": "yes_tok_123",
        "no_token_id": "no_tok_123",
        # endDate far in the future so time_remaining_fraction > 0
        "endDate": "2030-12-31T23:59:00Z",
        "startDate": "2026-03-16T00:00:00Z",
    }
    m.update(overrides)
    return m


def _make_engine() -> ArbitrageEngine:
    """Create an ArbitrageEngine with no external I/O dependencies."""
    engine = ArbitrageEngine(
        http_session=MagicMock(),
        binance_feed=None,
        smart_trader_tracker=None,
    )
    return engine


def _mock_bayesian(engine: ArbitrageEngine, probability: float) -> None:
    """Patch engine.bayesian.estimate to return a mock with .probability set."""
    result = MagicMock()
    result.probability = probability
    engine.bayesian.estimate = MagicMock(return_value=result)


def _mock_kelly(engine: ArbitrageEngine, size: float = 50.0) -> None:
    """Patch engine.kelly.position_size to return a fixed size."""
    engine.kelly.position_size = MagicMock(return_value=size)


def _mock_stoikov(engine: ArbitrageEngine, price: float = 0.45) -> None:
    """Patch engine.stoikov.adjusted_entry_price to return a fixed price."""
    engine.stoikov.adjusted_entry_price = MagicMock(return_value=price)


def _mock_edge_model(engine: ArbitrageEngine, single: float = 0.0, cross: float = 0.0) -> None:
    """Patch edge model so single_market_edge and cross_market_edge return known values."""
    engine.edge_model.single_market_edge = MagicMock(return_value=single)
    engine.edge_model.cross_market_edge = MagicMock(return_value=cross)
    # has_edge: always approve when edge > 0
    engine.edge_model.has_edge = MagicMock(side_effect=lambda e, threshold: e > threshold)


# ---------------------------------------------------------------------------
# Test 1: direction=YES → token_id == yes_token_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_yes_direction_uses_yes_token_id():
    """When YES edge dominates, token_id must be the yes_token_id."""
    engine = _make_engine()
    market = _base_market()

    # yes_ask=0.55 → bayesian_prob=0.75 → yes_edge=0.20 (strong YES)
    # no_ask=0.45 → no_prob=0.25 → no_edge=0.25-0.45 = -0.20 (negative)
    _mock_bayesian(engine, probability=0.75)
    _mock_kelly(engine, size=50.0)
    _mock_stoikov(engine, price=0.54)
    _mock_edge_model(engine, single=0.0, cross=0.0)
    # Force has_edge to True for positive edges
    engine.edge_model.has_edge = MagicMock(return_value=True)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None, "Expected a signal for strong YES edge"
    assert signal.direction == "YES"
    assert signal.token_id == market["yes_token_id"]
    assert signal.token_id == "yes_tok_123"


# ---------------------------------------------------------------------------
# Test 2: direction=NO → token_id == no_token_id (favorable conditions)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_direction_uses_no_token_id():
    """When NO edge dominates and conditions are met, token_id must be no_token_id."""
    engine = _make_engine()
    # YES is unlikely (prob=0.25), so NO edge is strong
    # yes_ask=0.55 → yes_edge = 0.25 - 0.55 = -0.30 (negative)
    # no_ask=0.45  → no_prob=0.75  → no_edge = 0.75 - 0.45 = 0.30 (positive)
    market = _base_market(best_ask="0.55", no_best_ask="0.45")

    _mock_bayesian(engine, probability=0.25)
    _mock_kelly(engine, size=50.0)
    _mock_stoikov(engine, price=0.44)
    _mock_edge_model(engine, single=0.0, cross=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None, "Expected a signal for strong NO edge with real book"
    assert signal.direction == "NO"
    assert signal.token_id == market["no_token_id"]
    assert signal.token_id == "no_tok_123"
    # Confirm yes_token_id was NOT used
    assert signal.token_id != market["yes_token_id"]


# ---------------------------------------------------------------------------
# Test 3: NO direction requires REAL_BOOK + no_side_health == "OK"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_direction_requires_real_book_and_healthy():
    """NO direction should only be selected when no_price_source == REAL_BOOK and health == OK."""
    engine = _make_engine()
    # Healthy real book: no_best_ask=0.45 → REAL_BOOK, health=OK
    market = _base_market(best_ask="0.55", no_best_ask="0.45")

    _mock_bayesian(engine, probability=0.25)  # strong NO edge
    _mock_kelly(engine, size=50.0)
    _mock_stoikov(engine, price=0.44)
    _mock_edge_model(engine, single=0.0, cross=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None
    assert signal.direction == "NO"

    diag = signal.side_diagnostics
    assert diag is not None
    assert diag.no_price_source == NoPriceSource.REAL_BOOK.value
    assert diag.selected_direction == "NO"
    assert diag.direction_reason == NoSideStatus.NO_EDGE_DOMINATES.value


# ---------------------------------------------------------------------------
# Test 4: NO with no_best_ask=0.99 → should NOT select NO direction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_direction_rejected_when_ask_is_099():
    """no_best_ask=0.99 is SUSPICIOUS/UNTRADABLE; NO must not be selected."""
    engine = _make_engine()
    # no_best_ask=0.99 → REAL_BOOK but UNTRADABLE (>= 0.95)
    market = _base_market(best_ask="0.55", no_best_ask="0.99")

    # bayesian_prob=0.25 → yes_edge=-0.30 (negative), no_prob=0.75
    # no_edge = 0.75 - 0.99 = -0.24 (also negative)
    # Both edges negative → BOTH_EDGES_NEGATIVE → return None
    _mock_bayesian(engine, probability=0.25)
    _mock_kelly(engine, size=50.0)
    _mock_stoikov(engine, price=0.98)
    _mock_edge_model(engine, single=0.0, cross=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    # The signal must be None because no valid direction can be selected
    assert signal is None, (
        "Expected no signal when no_best_ask=0.99 makes NO untradable and YES edge is negative"
    )


@pytest.mark.asyncio
async def test_no_direction_rejected_when_ask_is_suspicious_090():
    """no_best_ask >= 0.90 is SUSPICIOUS; even if NO edge theoretically exists, it must not be selected."""
    engine = _make_engine()
    # no_best_ask=0.91 → REAL_BOOK, health=SUSPICIOUS
    # bayesian_prob=0.15 → no_prob=0.85 → no_edge = 0.85 - 0.91 = -0.06 (negative anyway)
    # yes_edge = 0.15 - 0.55 = -0.40 (also negative)
    market = _base_market(best_ask="0.55", no_best_ask="0.91")

    _mock_bayesian(engine, probability=0.15)
    _mock_kelly(engine, size=50.0)
    _mock_stoikov(engine, price=0.90)
    _mock_edge_model(engine, single=0.0, cross=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    assert signal is None


# ---------------------------------------------------------------------------
# Test 5: NO without no_best_ask (None) → SYNTHETIC, NO must not be selected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_direction_rejected_when_no_best_ask_missing():
    """Without no_best_ask, source is SYNTHETIC; NO side must not be selected."""
    engine = _make_engine()
    # Remove no_best_ask entirely
    market = _base_market(best_ask="0.55")
    del market["no_best_ask"]

    # bayesian_prob=0.25 → yes_edge=-0.30 (negative)
    # no_price_ask = 1 - 0.55 = 0.45 (SYNTHETIC)
    # no_edge = 0.75 - 0.45 = 0.30 (positive, but source is SYNTHETIC)
    _mock_bayesian(engine, probability=0.25)
    _mock_kelly(engine, size=50.0)
    _mock_stoikov(engine, price=0.44)
    _mock_edge_model(engine, single=0.0, cross=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    # Must be None: SYNTHETIC source cannot select NO direction
    assert signal is None, "Expected no signal when no_best_ask is missing (SYNTHETIC source)"


@pytest.mark.asyncio
async def test_no_direction_rejected_when_no_best_ask_is_none():
    """Explicit no_best_ask=None must also result in SYNTHETIC price source."""
    engine = _make_engine()
    market = _base_market(best_ask="0.55", no_best_ask=None)

    _mock_bayesian(engine, probability=0.25)
    _mock_kelly(engine, size=50.0)
    _mock_stoikov(engine, price=0.44)
    _mock_edge_model(engine, single=0.0, cross=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    assert signal is None, "Expected no signal when no_best_ask is None (SYNTHETIC source)"

    # Also verify diagnostics record SYNTHETIC or MISSING (not REAL_BOOK)
    diag = engine._last_diagnostics.get("abc123")
    assert diag is not None
    assert diag.no_price_source != NoPriceSource.REAL_BOOK.value


# ---------------------------------------------------------------------------
# Test 6: TradeSignal.direction matches side_diagnostics.selected_direction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signal_direction_matches_diagnostics_selected_direction_yes():
    """For YES signals, TradeSignal.direction and diag.selected_direction must both be YES."""
    engine = _make_engine()
    market = _base_market(best_ask="0.55", no_best_ask="0.45")

    _mock_bayesian(engine, probability=0.75)  # strong YES edge
    _mock_kelly(engine, size=50.0)
    _mock_stoikov(engine, price=0.54)
    _mock_edge_model(engine, single=0.0, cross=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None
    assert signal.direction == "YES"
    assert signal.side_diagnostics is not None
    assert signal.side_diagnostics.selected_direction == signal.direction


@pytest.mark.asyncio
async def test_signal_direction_matches_diagnostics_selected_direction_no():
    """For NO signals, TradeSignal.direction and diag.selected_direction must both be NO."""
    engine = _make_engine()
    market = _base_market(best_ask="0.55", no_best_ask="0.45")

    _mock_bayesian(engine, probability=0.25)  # strong NO edge
    _mock_kelly(engine, size=50.0)
    _mock_stoikov(engine, price=0.44)
    _mock_edge_model(engine, single=0.0, cross=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None
    assert signal.direction == "NO"
    assert signal.side_diagnostics is not None
    assert signal.side_diagnostics.selected_direction == signal.direction


@pytest.mark.asyncio
async def test_signal_direction_none_when_all_edges_negative():
    """When both YES and NO edges are negative, no signal is returned and diag records NONE."""
    engine = _make_engine()
    # bayesian_prob=0.50, yes_ask=0.55 → yes_edge=-0.05; no_ask=0.45 → no_edge=0.50-0.45=0.05
    # Actually to get both negative: prob=0.50, yes_ask=0.55, no_ask=0.55 (sum > 1)
    market = _base_market(best_ask="0.55", no_best_ask="0.55")

    # prob=0.50 → yes_edge=-0.05, no_prob=0.50, no_edge=0.50-0.55=-0.05
    _mock_bayesian(engine, probability=0.50)
    _mock_kelly(engine, size=50.0)
    _mock_stoikov(engine, price=0.54)
    _mock_edge_model(engine, single=0.0, cross=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    assert signal is None
    diag = engine._last_diagnostics.get("abc123")
    assert diag is not None
    assert diag.selected_direction == "NONE"


# ---------------------------------------------------------------------------
# Test: NO direction never hardcodes YES token (regression guard)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_direction_never_uses_yes_token_id():
    """Guard against any regression where NO direction accidentally uses yes_token_id."""
    engine = _make_engine()
    market = _base_market(best_ask="0.55", no_best_ask="0.45")
    yes_tok = market["yes_token_id"]
    no_tok  = market["no_token_id"]
    assert yes_tok != no_tok, "Test market must have distinct token IDs"

    _mock_bayesian(engine, probability=0.25)
    _mock_kelly(engine, size=50.0)
    _mock_stoikov(engine, price=0.44)
    _mock_edge_model(engine, single=0.0, cross=0.0)
    engine.edge_model.has_edge = MagicMock(return_value=True)

    signal = await engine._evaluate_market(market, capital=1000.0, z_score=0.0, signal_type="bayesian")

    assert signal is not None
    assert signal.direction == "NO"
    # Core regression guard: NO must use NO token, never YES token
    assert signal.token_id == no_tok
    assert signal.token_id != yes_tok
