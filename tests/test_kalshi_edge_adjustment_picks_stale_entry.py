"""Regression test (84th daily review).

KalshiArbTracker.get_edge_adjustment()'s own docstring/comment says it uses
"en güncel Kalshi fiyatı" (the most recent Kalshi price) — but the
implementation picked `matching[0]`, i.e. whichever cached entry for the
asset happened to be FIRST in `self._cache`'s insertion order, not the one
with the newest `timestamp`. refresh() caches one entry per
(asset, ticker) pair and Kalshi's "status=open, limit=5" query routinely
returns several simultaneously-open contracts per asset (e.g. multiple
crypto strikes/expiries), so `self._cache` legitimately holds multiple
entries for the same asset at once, inserted across different refresh
cycles. `matching[0]` is that dict's positional first match, not the
newest one — a stale (but still <300s old) cached price for an older
ticker sitting earlier in the dict silently overrides an actually fresher
one, contradicting the function's own stated intent and feeding a wrong
(and here, sign-flipped) `_kalshi_adj` straight into
strategies/arbitrage_engine.py's bayesian_prob for a real live trade
decision.

Failure scenario:
  Cache holds two entries for "bitcoin": an OLDER one (yes_price=0.90,
  inserted first) and a NEWER one (yes_price=0.10, inserted second).
  get_edge_adjustment("bitcoin", poly_yes=0.50) should reflect the newer
  (fresher) 0.10 price, producing a NEGATIVE adjustment (Kalshi cheaper
  than Polymarket -> Polymarket looks overpriced -> edge reduced). The
  buggy code instead picks the stale 0.90 entry (first in insertion
  order) and returns a POSITIVE adjustment — the opposite sign.
"""
import time

from agents.kalshi_arb import KalshiArbTracker


def test_get_edge_adjustment_uses_freshest_cached_entry_not_first_inserted():
    tracker = KalshiArbTracker()
    now = time.time()

    # Older entry inserted FIRST — sits at index 0 in cache iteration order.
    tracker._cache["bitcoin_OLD-TICKER"] = {
        "yes_price": 0.90,
        "no_price": 0.10,
        "ticker": "OLD-TICKER",
        "title": "stale bitcoin contract",
        "timestamp": now - 200,  # still within the 300s freshness window
    }
    # Newer entry inserted SECOND — the one the docstring says should win.
    tracker._cache["bitcoin_NEW-TICKER"] = {
        "yes_price": 0.10,
        "no_price": 0.90,
        "ticker": "NEW-TICKER",
        "title": "fresh bitcoin contract",
        "timestamp": now - 10,
    }

    adjustment = tracker.get_edge_adjustment("bitcoin", poly_yes=0.50)

    # Fresh Kalshi price (0.10) is far below Polymarket's 0.50 -> Polymarket
    # looks overpriced relative to the true (fresh) cross-venue price ->
    # the adjustment must be negative (edge reduced), capped at -0.02.
    assert adjustment < 0, (
        f"expected a negative adjustment from the freshest cached price "
        f"(0.10 vs poly 0.50), got {adjustment} — picked a stale entry instead"
    )
    assert adjustment == -0.02
