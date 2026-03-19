"""
test_shadow_live_side_consistency.py

Unit tests verifying that the shadow journal and the live arbitrage path
apply identical NO-pricing logic and arrive at the same source classification.

No Orchestrator or ArbitrageEngine instances are created; the pricing logic is
replicated as pure helper functions matching the two code paths exactly.
"""
from __future__ import annotations

import pytest


# ─── Helpers replicating the live path (arbitrage_engine.py ~line 373-384) ────

LIVE_NO_UNTRADABLE_THRESHOLD = 0.95  # ask >= 0.95 → UNTRADABLE label


def live_no_price(market: dict, yes_price: float, no_book_fetched: bool) -> tuple[float, str]:
    """
    Return (no_price_ask, no_price_source) exactly as ArbitrageEngine computes them.

    no_book_fetched = True when no_best_ask key is present at all in the market dict
    (even if None), meaning the orderbook was actually queried.
    """
    real_no_ask = market.get("no_best_ask")
    if real_no_ask and float(real_no_ask) > 0:
        no_price_ask = float(real_no_ask)
        no_price_source = "REAL_BOOK"
    else:
        no_price_ask = round(1.0 - yes_price, 4)
        no_price_source = "SYNTHETIC" if not no_book_fetched else "MISSING"
    return no_price_ask, no_price_source


def live_no_health(no_price_ask: float, no_price_source: str) -> str:
    """
    Classify NO-side book health exactly as the engine does (PART 1D).
    Returns "OK", "UNTRADABLE", or "SUSPICIOUS".
    """
    if no_price_source != "REAL_BOOK":
        return "OK"  # health check only applies to real books
    if no_price_ask >= 0.95:
        return "UNTRADABLE"
    if no_price_ask >= 0.90:
        return "SUSPICIOUS"
    return "OK"


# ─── Helpers replicating the shadow path (orchestrator.py ~line 848-855) ──────

def shadow_no_price(market: dict, ask_yes: float, bid_yes: float) -> tuple[float, str]:
    """
    Return (_ask_no, _no_price_source) exactly as _record_shadow_decisions computes them.
    """
    real_no_ask = market.get("no_best_ask")
    if real_no_ask and float(real_no_ask) > 0:
        _ask_no = float(real_no_ask)
        _no_price_source = "REAL_BOOK"
    else:
        _ask_no = round(1.0 - (bid_yes if bid_yes > 0 else ask_yes), 4)
        _no_price_source = "SYNTHETIC"
    return _ask_no, _no_price_source


def shadow_mapping_context(
    _no_price_source: str,
    _ask_no: float,
    ew_status: str = "OK",
    dir_reason: str = "N/A",
) -> str:
    """Reproduce the mapping_context string written into the shadow journal."""
    return (
        f"no_src={_no_price_source} "
        f"no_ask={_ask_no:.4f} "
        f"ew={ew_status} "
        f"dir_reason={dir_reason}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — Real NO book present (0.45): both paths → REAL_BOOK, same value
# ══════════════════════════════════════════════════════════════════════════════

class TestRealBookNormalAsk:
    """no_best_ask = 0.45 — a healthy, tradable real book."""

    YES_PRICE = 0.55
    MARKET = {"no_best_ask": 0.45, "no_best_bid": 0.43}

    def test_live_source_is_real_book(self):
        _, src = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=True)
        assert src == "REAL_BOOK"

    def test_shadow_source_is_real_book(self):
        _, src = shadow_no_price(self.MARKET, ask_yes=self.YES_PRICE, bid_yes=0.54)
        assert src == "REAL_BOOK"

    def test_live_price_equals_market_value(self):
        price, _ = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=True)
        assert price == pytest.approx(0.45)

    def test_shadow_price_equals_market_value(self):
        price, _ = shadow_no_price(self.MARKET, ask_yes=self.YES_PRICE, bid_yes=0.54)
        assert price == pytest.approx(0.45)

    def test_prices_match_across_paths(self):
        live_price, live_src = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=True)
        shadow_price, shadow_src = shadow_no_price(
            self.MARKET, ask_yes=self.YES_PRICE, bid_yes=0.54
        )
        assert live_price == pytest.approx(shadow_price)
        assert live_src == shadow_src

    def test_health_is_ok(self):
        price, src = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=True)
        assert live_no_health(price, src) == "OK"


# ══════════════════════════════════════════════════════════════════════════════
# Test 2 — no_best_ask = None (book not present): both paths → SYNTHETIC
# ══════════════════════════════════════════════════════════════════════════════

class TestMissingNoBook:
    """no_best_ask is None; the NO orderbook was not queried at all."""

    YES_PRICE = 0.60
    BID_YES = 0.59
    MARKET: dict = {}  # no no_best_ask key at all

    def test_live_source_is_synthetic_when_no_book_not_fetched(self):
        _, src = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=False)
        assert src == "SYNTHETIC"

    def test_shadow_source_is_synthetic(self):
        _, src = shadow_no_price(self.MARKET, ask_yes=self.YES_PRICE, bid_yes=self.BID_YES)
        assert src == "SYNTHETIC"

    def test_live_synthetic_price_is_complement_of_yes(self):
        price, _ = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=False)
        assert price == pytest.approx(round(1.0 - self.YES_PRICE, 4))

    def test_shadow_synthetic_price_uses_bid_yes_when_available(self):
        price, _ = shadow_no_price(self.MARKET, ask_yes=self.YES_PRICE, bid_yes=self.BID_YES)
        assert price == pytest.approx(round(1.0 - self.BID_YES, 4))

    def test_shadow_synthetic_falls_back_to_ask_when_bid_zero(self):
        price, _ = shadow_no_price(self.MARKET, ask_yes=self.YES_PRICE, bid_yes=0.0)
        assert price == pytest.approx(round(1.0 - self.YES_PRICE, 4))

    def test_sources_agree_as_synthetic(self):
        _, live_src = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=False)
        _, shadow_src = shadow_no_price(self.MARKET, ask_yes=self.YES_PRICE, bid_yes=self.BID_YES)
        assert live_src == "SYNTHETIC"
        assert shadow_src == "SYNTHETIC"


# ══════════════════════════════════════════════════════════════════════════════
# Test 3 — no_best_ask = 0.99 (real book, but untradable)
# ══════════════════════════════════════════════════════════════════════════════

class TestUntradableNoBook:
    """
    no_best_ask = 0.99 — real book is present but ask is >= 0.95.
    Live: REAL_BOOK / UNTRADABLE health.
    Shadow: REAL_BOOK with value 0.99 (shadow does not health-check, just records).
    """

    YES_PRICE = 0.01
    MARKET = {"no_best_ask": 0.99, "no_best_bid": 0.0}

    def test_live_source_is_real_book(self):
        _, src = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=True)
        assert src == "REAL_BOOK"

    def test_live_health_is_untradable(self):
        price, src = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=True)
        assert live_no_health(price, src) == "UNTRADABLE"

    def test_live_price_is_0_99(self):
        price, _ = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=True)
        assert price == pytest.approx(0.99)

    def test_shadow_source_is_real_book(self):
        _, src = shadow_no_price(self.MARKET, ask_yes=self.YES_PRICE, bid_yes=0.0)
        assert src == "REAL_BOOK"

    def test_shadow_price_is_0_99(self):
        price, _ = shadow_no_price(self.MARKET, ask_yes=self.YES_PRICE, bid_yes=0.0)
        assert price == pytest.approx(0.99)

    def test_both_see_real_book_with_same_value(self):
        live_price, live_src = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=True)
        shadow_price, shadow_src = shadow_no_price(
            self.MARKET, ask_yes=self.YES_PRICE, bid_yes=0.0
        )
        assert live_src == shadow_src == "REAL_BOOK"
        assert live_price == pytest.approx(shadow_price)


# ══════════════════════════════════════════════════════════════════════════════
# Test 4 — no_best_ask = 0 (key present but zero → treated as missing)
# ══════════════════════════════════════════════════════════════════════════════

class TestZeroNoAsk:
    """
    no_best_ask = 0 is falsy; both paths must fall through to synthetic.
    The book was fetched (no_book_fetched=True), so live path → MISSING.
    Shadow does not have that distinction; it always calls the result SYNTHETIC.
    """

    YES_PRICE = 0.55
    BID_YES = 0.54
    MARKET = {"no_best_ask": 0, "no_best_bid": 0}  # zero = effectively absent

    def test_live_does_not_use_zero_as_real_book(self):
        price, src = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=True)
        assert src != "REAL_BOOK"

    def test_live_source_is_missing_when_book_was_fetched(self):
        _, src = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=True)
        assert src == "MISSING"

    def test_live_source_is_synthetic_when_book_not_fetched(self):
        _, src = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=False)
        assert src == "SYNTHETIC"

    def test_shadow_does_not_use_zero_as_real_book(self):
        _, src = shadow_no_price(self.MARKET, ask_yes=self.YES_PRICE, bid_yes=self.BID_YES)
        assert src != "REAL_BOOK"

    def test_shadow_falls_through_to_synthetic(self):
        _, src = shadow_no_price(self.MARKET, ask_yes=self.YES_PRICE, bid_yes=self.BID_YES)
        assert src == "SYNTHETIC"

    def test_live_synthetic_price_is_complement(self):
        price, _ = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=True)
        assert price == pytest.approx(round(1.0 - self.YES_PRICE, 4))

    def test_shadow_synthetic_price_uses_bid_yes(self):
        price, _ = shadow_no_price(self.MARKET, ask_yes=self.YES_PRICE, bid_yes=self.BID_YES)
        assert price == pytest.approx(round(1.0 - self.BID_YES, 4))

    def test_prices_match_when_bid_yes_equals_yes_price(self):
        """When bid_yes == yes_price both synthetic prices should be identical."""
        price_live, _ = live_no_price(self.MARKET, self.YES_PRICE, no_book_fetched=False)
        price_shadow, _ = shadow_no_price(
            self.MARKET, ask_yes=self.YES_PRICE, bid_yes=self.YES_PRICE
        )
        assert price_live == pytest.approx(price_shadow)


# ══════════════════════════════════════════════════════════════════════════════
# Test 5 — mapping_context in shadow includes no_src field
# ══════════════════════════════════════════════════════════════════════════════

class TestMappingContextIncludesNoSrc:
    """The mapping_context string written into the shadow journal must expose no_src."""

    def test_real_book_context_contains_no_src(self):
        ctx = shadow_mapping_context("REAL_BOOK", 0.45)
        assert "no_src=REAL_BOOK" in ctx

    def test_synthetic_context_contains_no_src(self):
        ctx = shadow_mapping_context("SYNTHETIC", 0.40)
        assert "no_src=SYNTHETIC" in ctx

    def test_context_contains_no_ask_value(self):
        ctx = shadow_mapping_context("REAL_BOOK", 0.45)
        assert "no_ask=0.4500" in ctx

    def test_context_contains_ew_status(self):
        ctx = shadow_mapping_context("REAL_BOOK", 0.45, ew_status="STALE")
        assert "ew=STALE" in ctx

    def test_context_contains_dir_reason(self):
        ctx = shadow_mapping_context("REAL_BOOK", 0.45, dir_reason="YES_EDGE_DOMINATES")
        assert "dir_reason=YES_EDGE_DOMINATES" in ctx

    def test_context_format_matches_expected_order(self):
        ctx = shadow_mapping_context("SYNTHETIC", 0.38, ew_status="OK", dir_reason="N/A")
        # Fields must appear in the documented order
        no_src_pos = ctx.index("no_src=")
        no_ask_pos = ctx.index("no_ask=")
        ew_pos = ctx.index("ew=")
        dir_pos = ctx.index("dir_reason=")
        assert no_src_pos < no_ask_pos < ew_pos < dir_pos

    def test_real_book_context_end_to_end(self):
        market = {"no_best_ask": 0.45}
        _, src = shadow_no_price(market, ask_yes=0.55, bid_yes=0.54)
        price, _ = shadow_no_price(market, ask_yes=0.55, bid_yes=0.54)
        ctx = shadow_mapping_context(src, price)
        assert "no_src=REAL_BOOK" in ctx
        assert "no_ask=0.4500" in ctx

    def test_synthetic_context_end_to_end(self):
        market: dict = {}
        price, src = shadow_no_price(market, ask_yes=0.60, bid_yes=0.59)
        ctx = shadow_mapping_context(src, price)
        assert "no_src=SYNTHETIC" in ctx
        assert f"no_ask={price:.4f}" in ctx


# ══════════════════════════════════════════════════════════════════════════════
# Test 6 — edge / regression: string "0" and string "0.45" inputs
# ══════════════════════════════════════════════════════════════════════════════

class TestStringInputsCoercedCorrectly:
    """
    Market dict values from JSON / API responses may arrive as strings.
    Both paths call float() on the value, which must not silently misbehave.
    """

    def test_live_string_zero_falls_to_synthetic(self):
        market = {"no_best_ask": "0"}
        # float("0") == 0.0 which is falsy → should fall through
        price, src = live_no_price(market, yes_price=0.55, no_book_fetched=True)
        assert src != "REAL_BOOK"

    def test_shadow_string_zero_falls_to_synthetic(self):
        market = {"no_best_ask": "0"}
        _, src = shadow_no_price(market, ask_yes=0.55, bid_yes=0.54)
        assert src != "REAL_BOOK"

    def test_live_string_float_parsed_as_real_book(self):
        market = {"no_best_ask": "0.45"}
        price, src = live_no_price(market, yes_price=0.55, no_book_fetched=True)
        assert src == "REAL_BOOK"
        assert price == pytest.approx(0.45)

    def test_shadow_string_float_parsed_as_real_book(self):
        market = {"no_best_ask": "0.45"}
        price, src = shadow_no_price(market, ask_yes=0.55, bid_yes=0.54)
        assert src == "REAL_BOOK"
        assert price == pytest.approx(0.45)
