"""
Regression test (48th daily review): BondScanner._evaluate_market's NO-side
synthetic price estimate was missing the exact spread-inflation correction
that strategies/arbitrage_engine.py already applies for the identical
situation (no real NO orderbook available, so NO's ask must be estimated
from YES's ask).

strategies/arbitrage_engine.py's `_evaluate_market` documents this directly:

    # FIX-3: NO SYNTHETIC PRICING FIX — add spread estimate to avoid inflation
    no_price_ask = round(1.0 - yes_price + 0.02, 4)  # was 1.0 - yes_price (too optimistic)

i.e. `1.0 - yes_price` alone was found, in production, to systematically
UNDER-estimate the real NO ask (it ignores the bid/ask spread — the real
identity is NO_ask ≈ 1 - YES_bid, not 1 - YES_ask), so a +0.02 conservative
pad was added there.

`strategies/bond_scanner.py::BondScanner._evaluate_market()` needs the exact
same estimate for the exact same reason: `BondScanner.scan()` calls
`client.get_all_active_markets()`, which (unlike `get_active_markets()`) never
populates `no_best_ask` at all — so bond_scanner has only the YES ask to work
with. But its NO-side branch still used the bare, already-proven-too-optimistic
`no_price_est = 1.0 - yes_price`, with no pad:

    if yes_price <= (1.0 - self.PROB_THRESHOLD) and no_token:
        no_price_est = 1.0 - yes_price  # approximate NO price
        ...
        if self.PROB_THRESHOLD <= no_price_est <= self.MAX_PRICE:
            yld = (1.0 - no_price_est) / no_price_est
            if yld >= self.MIN_YIELD:
                return BondOpportunity(..., price=no_price_est, ...)

Concrete failure scenario: yes_price=0.03 (a real, plausible "near-certain
NO" market). Unpadded: no_price_est = 1.0 - 0.03 = 0.97, which lands right at
the top of BondScanner's own [PROB_THRESHOLD=0.93, MAX_PRICE=0.97] acceptance
band with yld = 3.09% (just above MIN_YIELD=0.03) — so BondScanner reports a
"near-certain, 3%+ yield" NO opportunity and `Orchestrator._bond_cycle()`
places a REAL GTC buy order for it via `client.place_passive_order(price=0.97,
...)`. Applying the exact same +0.02 pad already proven correct in
arbitrage_engine.py gives no_price_est = 0.99, which is ABOVE MAX_PRICE
(0.97) — correctly recognizing that the true NO ask for a market this
illiquid/extreme is very likely NOT actually a legitimate sub-97c "near
certain" price, and the opportunity should be rejected, not traded.
"""
from __future__ import annotations

from strategies.bond_scanner import BondScanner


def _market(**overrides) -> dict:
    base = {
        "best_ask": 0.03,
        "best_bid": 0.02,
        "volume": 50_000.0,
        "question": "Will XYZ happen?",
        "condition_id": "cond_no_side_synth",
        "yes_token_id": "yes_tok",
        "no_token_id": "no_tok",
        "end_date_iso": "2026-09-20T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_no_side_synthetic_price_uses_same_spread_pad_as_arbitrage_engine():
    """no_price_est must be padded like arbitrage_engine.py's FIX-3, not bare 1.0-yes_price."""
    scanner = BondScanner()
    import datetime as _dt
    now = _dt.datetime(2026, 9, 15, tzinfo=_dt.timezone.utc)

    market = _market(best_ask=0.03)
    opp = scanner._evaluate_market(market, now)

    # Pre-fix: no_price_est = 1.0 - 0.03 = 0.97 -> within [0.93, 0.97] -> ACCEPTED
    # (this assertion is what fails against the unfixed code)
    assert opp is None, (
        f"BondScanner accepted a NO-side opportunity at price={opp.price if opp else None} "
        "computed from the unpadded 1.0-yes_price estimate; the same synthetic-NO-price "
        "situation in arbitrage_engine.py requires a +0.02 spread pad (FIX-3) precisely "
        "because this bare estimate is too optimistic."
    )


def test_no_side_synthetic_price_still_finds_real_opportunities():
    """A genuinely cheap YES (bigger margin) must still be identified as a NO bond, padded."""
    scanner = BondScanner()
    import datetime as _dt
    now = _dt.datetime(2026, 9, 15, tzinfo=_dt.timezone.utc)

    # yes_price=0.01 -> padded no_price_est = 1.0 - 0.01 + 0.02 = 1.01 (still too rich, actually)
    # Use yes_price that leaves room for the +0.02 pad and still lands in-band.
    market = _market(best_ask=0.055)
    opp = scanner._evaluate_market(market, now)

    assert opp is not None
    assert opp.side == "NO"
    # Padded estimate: 1.0 - 0.055 + 0.02 = 0.965
    assert abs(opp.price - 0.965) < 1e-9
