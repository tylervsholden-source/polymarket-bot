"""
Regression test: `RealtimeFeed`'in canlı WS aboneliği, ilk `BinanceFeed.refresh()`
çağrısında o cycle'da hangi coin'lerin marketi açıksa SADECE onlara kilitleniyordu.

Bug (agents/binance_feed.py::refresh(), before this fix):

    if not self._ws_started:
        self._ws_started = True
        ...
        self._ws_feed = RealtimeFeed()
        self._ws_feed.start(symbols)   # o cycle'ın (kısmi) market listesi

`symbols`, `refresh_spot_data()` tarafından o anki `markets` listesinden
(`_detect_asset()` ile) türetiliyor — yani sabit bir evren değil, o cycle'da
Polymarket'te aktif olan marketlere bağlı. Bot boot olduğunda ya da bir coin'in
5 dakikalık marketi henüz açılmamış/rollover arasındaysa, ilk `refresh()`
çağrısındaki `symbols` 6 coin'in (BTC/ETH/SOL/XRP/DOGE/BNB) hepsini
içermeyebilir. `_ws_started` bayrağı bir daha asla `False` olmadığından ve
`RealtimeFeed.start()` zaten çalışıyorsa hiçbir şey yapmadığından, o ilk
çağrıda eksik olan coin(ler) sürecin ömrü boyunca WS aboneliği hiç almıyor —
`RT_LAG_BLOCK_YES/NO` gate'i (agents/binance_feed.py::get_recent_change() →
strategies/arbitrage_engine.py) o coin için WS'in çözdüğü "coarse per-cycle
history nadiren 60s pencerede 2 nokta tutar" sorununa (bkz.
test_realtime_lag_prevention_cadence.py) geri düşüyor ve pratikte hiç
tetiklenmiyor.

Fix: `RealtimeFeed.start()`'ın zaten `symbols=None` → tüm bilinen
`_WS_PAIRS` evrenine abone olma davranışı var; `refresh()` artık cycle'a
özgü kısmi kümeyi değil, hep bu tam evreni kullanıyor.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.binance_feed import BinanceFeed
from agents.ws_feed import _WS_PAIRS


class _CaptureRealtimeFeed:
    """RealtimeFeed yerine geçip start()'a hangi symbols kümesinin
    verildiğini yakalar — gerçek WS/thread açmadan."""

    instances: list["_CaptureRealtimeFeed"] = []

    def __init__(self):
        self.started_with = "NOT_CALLED"
        _CaptureRealtimeFeed.instances.append(self)

    def start(self, symbols=None):
        self.started_with = symbols
        return True


@pytest.mark.asyncio
async def test_ws_feed_start_covers_full_universe_even_with_partial_first_cycle(monkeypatch):
    _CaptureRealtimeFeed.instances.clear()

    feed = BinanceFeed.__new__(BinanceFeed)  # __init__ atla, ağ yok
    feed._ws_feed = None
    feed._ws_started = False
    feed._price_history = {}
    feed._cache = {}
    feed.enhanced = AsyncMock()

    monkeypatch.setattr("agents.ws_feed.RealtimeFeed", _CaptureRealtimeFeed)
    monkeypatch.setattr(feed, "_init_exchanges", AsyncMock(return_value=None))
    monkeypatch.setattr(feed, "_fetch_fear_greed", AsyncMock(return_value=None))
    monkeypatch.setattr(feed, "_fetch_binance_spot_prices", AsyncMock(return_value=None))
    monkeypatch.setattr(feed, "_fetch_funding_oi", AsyncMock(return_value=None))
    monkeypatch.setattr(feed, "_fetch_liquidations", AsyncMock(return_value=None))
    monkeypatch.setattr(feed, "_fetch_long_short_ratio", AsyncMock(return_value=None))
    monkeypatch.setattr(feed, "_fetch_spx", AsyncMock(return_value=None))
    monkeypatch.setattr(feed, "_fetch_symbol", AsyncMock(return_value=None))

    # İlk cycle'da sadece BTC marketi açık (bot boot / diğer coin'lerin
    # 5dk marketi henüz rollover olmadı) -> kısmi symbols kümesi.
    await feed.refresh({"BTCUSDT"})

    assert len(_CaptureRealtimeFeed.instances) == 1
    rf = _CaptureRealtimeFeed.instances[0]
    # WS aboneliği, o cycle'a özgü kısmi kümeyle değil, botun bilinen tüm
    # coin evreniyle başlamalı -- sonradan eklenecek bir şans yok
    # (_ws_started ikinci refresh()'te tekrar start() çağırmaz).
    covered = set(_WS_PAIRS.keys()) if rf.started_with is None else set(rf.started_with)
    assert covered >= set(_WS_PAIRS.keys()), (
        f"WS feed only subscribed to {covered}, missing "
        f"{set(_WS_PAIRS.keys()) - covered} for the life of the process"
    )
