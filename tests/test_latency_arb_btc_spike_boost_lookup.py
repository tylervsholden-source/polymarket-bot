"""
get_spike_boost()'un reverse coin->symbol lookup'i, cagiran tarafin (arbitrage_engine.py)
gonderdigi "btc"/"eth"/... prefix'i degil, insan-okunur COIN_KEYWORDS degerini
("bitcoin", "ethereum", ...) substring eslestiriyordu. "btc" harfleri "bitcoin"
icinde ardisik gecmedigi icin (b-i-t-c-o-i-n) BTC hicbir zaman eslesmiyor ve
fonksiyon her zaman 0.0 donuyordu; diger tum coinler (eth/sol/xrp/doge/bnb)
kw'nin basiyla ayni oldugu icin tesadufen eslesiyordu.
"""
from agents.latency_arb import LatencyArbEngine, LatencyArbConfig, SpikeSignal, COIN_KEYWORDS


def _make_engine() -> LatencyArbEngine:
    return LatencyArbEngine(client=None, position_manager=None, config=LatencyArbConfig())


def _inject_spike(engine: LatencyArbEngine, symbol: str, change_pct: float) -> None:
    engine._recent_spikes.append(SpikeSignal(
        symbol=symbol,
        direction="UP" if change_pct > 0 else "DOWN",
        change_pct=change_pct,
        vwap_before=100.0,
        vwap_after=100.0 * (1 + change_pct / 100.0),
        detected_at=__import__("time").time(),
    ))


def test_btc_spike_boost_matches_like_every_other_coin():
    engine = _make_engine()
    _inject_spike(engine, "BTCUSDT", change_pct=0.16)  # -> 0.016 boost (10x, uncapped)

    boost = engine.get_spike_boost("btc", max_age_sec=30.0)

    assert boost != 0.0, "BTC spike boost must not silently collapse to 0.0"
    assert boost == 0.016


def test_all_coin_prefixes_resolve_to_their_own_symbol():
    engine = _make_engine()
    for sym in COIN_KEYWORDS:
        prefix = sym.replace("USDT", "").lower()
        _inject_spike(engine, sym, change_pct=0.10)
        boost = engine.get_spike_boost(prefix, max_age_sec=30.0)
        assert boost != 0.0, f"{sym} (prefix={prefix!r}) failed to resolve to its own spike"


def test_unknown_coin_prefix_returns_zero():
    engine = _make_engine()
    _inject_spike(engine, "BTCUSDT", change_pct=0.16)

    assert engine.get_spike_boost("notacoin", max_age_sec=30.0) == 0.0
