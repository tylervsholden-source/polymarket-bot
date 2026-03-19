"""
Enhanced Signal Sources — GitHub entegrasyonları.

1. Multi-exchange orderflow (ccxt: OKX, Bybit, Kraken)
2. Deribit options data (max pain, put/call ratio, IV)
3. On-chain whale transfers (blockchain.info, Etherscan)
4. Social sentiment (CryptoCompare, Reddit)
5. Aggregate orderflow imbalance
"""
from __future__ import annotations

import asyncio
import time
from loguru import logger

try:
    import ccxt.async_support as ccxt_async
    _CCXT = True
except ImportError:
    _CCXT = False


class EnhancedSignals:
    """All new signal sources in one class. Plugs into BinanceFeed.refresh()."""

    _SYMBOL_MAP = {
        "BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT", "SOLUSDT": "SOL/USDT",
        "XRPUSDT": "XRP/USDT", "DOGEUSDT": "DOGE/USDT", "BNBUSDT": "BNB/USDT",
    }
    _DERIBIT_MAP = {
        "BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL",
    }
    _BLOCKCHAIN_MAP = {
        "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum",
    }

    def __init__(self, http_session=None):
        self.session = http_session
        # Multi-exchange prices
        self._exchange_prices: dict[str, dict[str, float]] = {}  # symbol → {exchange: price}
        self._exchange_orderflow: dict[str, dict] = {}            # symbol → {buy_vol, sell_vol, imbalance}
        self._exchange_last_fetch: float = 0.0
        # Options data
        self._options_data: dict[str, dict] = {}  # symbol → {max_pain, pcr, iv}
        self._options_last_fetch: float = 0.0
        # Whale data
        self._whale_data: dict[str, dict] = {}    # symbol → {large_txs, net_flow, signal}
        self._whale_last_fetch: float = 0.0
        # Social sentiment
        self._social_data: dict[str, dict] = {}   # symbol → {score, volume, signal}
        self._social_last_fetch: float = 0.0
        # ccxt exchanges (lazy init)
        self._exchanges: dict[str, object] = {}
        self._exchanges_initialized = False

    async def _init_ccxt(self):
        """Initialize ccxt exchange instances (OKX, Bybit, Kraken)."""
        if self._exchanges_initialized or not _CCXT:
            return
        self._exchanges_initialized = True
        try:
            self._exchanges = {
                "okx": ccxt_async.okx({"enableRateLimit": True, "timeout": 5000}),
                "bybit": ccxt_async.bybit({"enableRateLimit": True, "timeout": 5000}),
                "kraken": ccxt_async.kraken({"enableRateLimit": True, "timeout": 5000}),
            }
            logger.info(f"CCXT: {len(self._exchanges)} exchange initialized (OKX, Bybit, Kraken)")
        except Exception as e:
            logger.debug(f"CCXT init failed: {e}")
            self._exchanges = {}

    async def close(self):
        """Close ccxt exchange connections."""
        for name, ex in self._exchanges.items():
            try:
                await ex.close()
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════════════
    # 1. MULTI-EXCHANGE ORDERFLOW + PRICES
    # ═══════════════════════════════════════════════════════════════════════

    async def fetch_multi_exchange(self, symbols: set[str]) -> None:
        """Fetch prices + orderbook from OKX, Bybit, Kraken via ccxt."""
        if not _CCXT:
            return
        now = time.time()
        if now - self._exchange_last_fetch < 30:  # 30s cache
            return
        self._exchange_last_fetch = now

        await self._init_ccxt()

        for symbol in symbols:
            ccxt_sym = self._SYMBOL_MAP.get(symbol)
            if not ccxt_sym:
                continue

            prices = {}
            total_buy_vol = 0.0
            total_sell_vol = 0.0

            for ex_name, ex in self._exchanges.items():
                try:
                    ticker = await ex.fetch_ticker(ccxt_sym)
                    if ticker and ticker.get("last"):
                        prices[ex_name] = ticker["last"]
                        # Use bid/ask volume as proxy for orderflow
                        bid_vol = ticker.get("bidVolume", 0) or 0
                        ask_vol = ticker.get("askVolume", 0) or 0
                        total_buy_vol += bid_vol
                        total_sell_vol += ask_vol
                except Exception:
                    pass

            if prices:
                self._exchange_prices[symbol] = prices

                # Orderflow imbalance: positive = buying pressure
                total = total_buy_vol + total_sell_vol
                imbalance = (total_buy_vol - total_sell_vol) / total if total > 0 else 0.0

                self._exchange_orderflow[symbol] = {
                    "buy_vol": total_buy_vol,
                    "sell_vol": total_sell_vol,
                    "imbalance": round(imbalance, 4),
                    "exchange_count": len(prices),
                }

        if self._exchange_prices:
            logger.info(
                f"MULTI_EX: {len(self._exchange_prices)} coins from "
                f"{len(self._exchanges)} exchanges"
            )

    def get_multi_exchange_signal(self, symbol: str) -> dict:
        """Returns consensus from OKX/Bybit/Kraken prices + orderflow.

        Returns:
            {
                "prices": {exchange: price},
                "spread_pct": float,     # max spread across exchanges
                "orderflow": float,      # -1 (sell) to +1 (buy)
                "signal": str,           # BULLISH/BEARISH/NEUTRAL
                "boost": float,          # -0.02 to +0.02
            }
        """
        prices = self._exchange_prices.get(symbol, {})
        flow = self._exchange_orderflow.get(symbol, {})

        if not prices:
            return {"prices": {}, "spread_pct": 0, "orderflow": 0,
                    "signal": "NEUTRAL", "boost": 0.0}

        vals = list(prices.values())
        spread = (max(vals) - min(vals)) / min(vals) * 100 if vals else 0
        imbalance = flow.get("imbalance", 0)

        # Signal: orderflow imbalance > 0.2 = directional
        if imbalance > 0.20:
            signal = "BULLISH"
            boost = min(0.02, imbalance * 0.05)
        elif imbalance < -0.20:
            signal = "BEARISH"
            boost = max(-0.02, imbalance * 0.05)
        else:
            signal = "NEUTRAL"
            boost = 0.0

        return {
            "prices": prices,
            "spread_pct": round(spread, 4),
            "orderflow": imbalance,
            "signal": signal,
            "boost": round(boost, 4),
        }

    # ═══════════════════════════════════════════════════════════════════════
    # 2. DERIBIT OPTIONS (Max Pain, Put/Call Ratio, IV)
    # ═══════════════════════════════════════════════════════════════════════

    async def fetch_options_data(self, symbols: set[str]) -> None:
        """Fetch options data from Deribit (BTC, ETH, SOL only)."""
        now = time.time()
        if now - self._options_last_fetch < 300:  # 5 min cache
            return
        self._options_last_fetch = now

        if not self.session:
            return

        for symbol in symbols:
            deribit_coin = self._DERIBIT_MAP.get(symbol)
            if not deribit_coin:
                continue
            try:
                # Deribit public API — no key needed
                resp = await self.session.get(
                    "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                    params={"currency": deribit_coin, "kind": "option"},
                )
                if resp.status_code != 200:
                    continue

                data = resp.json().get("result", [])
                if not data:
                    continue

                # Calculate put/call ratio and aggregate IV
                call_oi = 0.0
                put_oi = 0.0
                call_volume = 0.0
                put_volume = 0.0
                iv_sum = 0.0
                iv_count = 0

                for opt in data:
                    name = opt.get("instrument_name", "")
                    oi = opt.get("open_interest", 0) or 0
                    vol = opt.get("volume", 0) or 0
                    mark_iv = opt.get("mark_iv", 0) or 0

                    if "-C" in name:  # Call
                        call_oi += oi
                        call_volume += vol
                    elif "-P" in name:  # Put
                        put_oi += oi
                        put_volume += vol

                    if mark_iv > 0:
                        iv_sum += mark_iv
                        iv_count += 1

                pcr_oi = put_oi / call_oi if call_oi > 0 else 1.0
                pcr_vol = put_volume / call_volume if call_volume > 0 else 1.0
                avg_iv = iv_sum / iv_count if iv_count > 0 else 0

                self._options_data[symbol] = {
                    "pcr_oi": round(pcr_oi, 3),
                    "pcr_volume": round(pcr_vol, 3),
                    "avg_iv": round(avg_iv, 2),
                    "call_oi": call_oi,
                    "put_oi": put_oi,
                }
                logger.info(
                    f"DERIBIT: {deribit_coin} PCR(OI)={pcr_oi:.2f} PCR(Vol)={pcr_vol:.2f} "
                    f"IV={avg_iv:.1f}% Calls={call_oi:.0f} Puts={put_oi:.0f}"
                )
            except Exception as e:
                logger.debug(f"Deribit fetch failed for {deribit_coin}: {e}")

    def get_options_signal(self, symbol: str) -> dict:
        """Interpret options data into directional signal.

        PCR > 1.2 = bearish sentiment (more puts) → contrarian BULLISH
        PCR < 0.7 = bullish sentiment (more calls) → contrarian BEARISH
        IV > 80 = high fear → potential reversal

        Returns:
            {"pcr": float, "iv": float, "signal": str, "boost": float}
        """
        data = self._options_data.get(symbol, {})
        if not data:
            return {"pcr": 0, "iv": 0, "signal": "NEUTRAL", "boost": 0.0}

        pcr = data.get("pcr_oi", 1.0)
        iv = data.get("avg_iv", 0)

        # Contrarian: extreme PCR = opposite signal
        if pcr > 1.3:
            # Heavy puts = crowd bearish → contrarian bullish
            signal = "BULLISH"
            boost = min(0.02, (pcr - 1.0) * 0.04)
        elif pcr < 0.6:
            # Heavy calls = crowd bullish → contrarian bearish
            signal = "BEARISH"
            boost = max(-0.02, (pcr - 1.0) * 0.04)
        else:
            signal = "NEUTRAL"
            boost = 0.0

        # High IV amplifies signal (fear = bigger contrarian move)
        if iv > 80 and boost != 0:
            boost *= 1.3

        return {
            "pcr": round(pcr, 3),
            "iv": round(iv, 1),
            "signal": signal,
            "boost": round(boost, 4),
        }

    # ═══════════════════════════════════════════════════════════════════════
    # 3. ON-CHAIN WHALE DETECTION
    # ═══════════════════════════════════════════════════════════════════════

    async def fetch_whale_data(self, symbols: set[str]) -> None:
        """Track large on-chain transfers via public APIs."""
        now = time.time()
        if now - self._whale_last_fetch < 300:  # 5 min cache
            return
        self._whale_last_fetch = now

        if not self.session:
            return

        # BTC: blockchain.info large transactions
        if "BTCUSDT" in symbols:
            try:
                resp = await self.session.get(
                    "https://blockchain.info/unconfirmed-transactions?format=json",
                )
                if resp.status_code == 200:
                    txs = resp.json().get("txs", [])
                    # Find large transactions (>10 BTC ≈ >$700k)
                    large_txs = []
                    exchange_inflow = 0.0
                    exchange_outflow = 0.0
                    for tx in txs[:100]:  # Check last 100
                        total_out = sum(o.get("value", 0) for o in tx.get("out", []))
                        btc_amount = total_out / 1e8
                        if btc_amount > 10:  # >10 BTC
                            large_txs.append({
                                "amount_btc": round(btc_amount, 2),
                                "hash": tx.get("hash", "")[:16],
                            })

                    self._whale_data["BTCUSDT"] = {
                        "large_tx_count": len(large_txs),
                        "largest_btc": max((t["amount_btc"] for t in large_txs), default=0),
                        "signal": "WHALE_ACTIVE" if len(large_txs) > 5 else "NORMAL",
                    }
                    if large_txs:
                        logger.info(
                            f"WHALE_BTC: {len(large_txs)} large txs, "
                            f"biggest={self._whale_data['BTCUSDT']['largest_btc']:.1f} BTC"
                        )
            except Exception as e:
                logger.debug(f"Whale BTC fetch failed: {e}")

        # ETH: Etherscan large transfers (no API key needed for basic)
        if "ETHUSDT" in symbols:
            try:
                # Use CryptoCompare for ETH on-chain summary instead
                resp = await self.session.get(
                    "https://min-api.cryptocompare.com/data/blockchain/latest",
                    params={"fsym": "ETH"},
                )
                if resp.status_code == 200:
                    bc_data = resp.json().get("Data", {})
                    large_tx_vol = bc_data.get("large_transaction_volume", 0)
                    active_addr = bc_data.get("active_addresses", 0)

                    self._whale_data["ETHUSDT"] = {
                        "large_tx_volume": large_tx_vol,
                        "active_addresses": active_addr,
                        "signal": "WHALE_ACTIVE" if large_tx_vol > 0 else "NORMAL",
                    }
                    if large_tx_vol > 0:
                        logger.info(f"WHALE_ETH: large_vol={large_tx_vol:.0f} active_addr={active_addr}")
            except Exception as e:
                logger.debug(f"Whale ETH fetch failed: {e}")

    def get_whale_signal(self, symbol: str) -> dict:
        """Returns whale activity signal.

        High whale activity = potential big move incoming.
        Not directional on its own — amplifies other signals.

        Returns:
            {"active": bool, "boost_multiplier": float, "detail": str}
        """
        data = self._whale_data.get(symbol, {})
        if not data or data.get("signal") == "NORMAL":
            return {"active": False, "boost_multiplier": 1.0, "detail": "no whale activity"}

        # Whale activity amplifies conviction (1.15x boost to whatever direction)
        return {
            "active": True,
            "boost_multiplier": 1.15,
            "detail": f"whale active: {data}",
        }

    # ═══════════════════════════════════════════════════════════════════════
    # 4. SOCIAL SENTIMENT (CryptoCompare)
    # ═══════════════════════════════════════════════════════════════════════

    _SOCIAL_IDS = {
        "BTCUSDT": "1182", "ETHUSDT": "7605", "SOLUSDT": "934443",
        "XRPUSDT": "5031", "DOGEUSDT": "4432", "BNBUSDT": "204788",
    }

    async def fetch_social_sentiment(self, symbols: set[str]) -> None:
        """Fetch social sentiment from CryptoCompare."""
        now = time.time()
        if now - self._social_last_fetch < 300:  # 5 min cache
            return
        self._social_last_fetch = now

        if not self.session:
            return

        for symbol in symbols:
            coin_id = self._SOCIAL_IDS.get(symbol)
            if not coin_id:
                continue
            try:
                resp = await self.session.get(
                    "https://min-api.cryptocompare.com/data/social/coin/latest",
                    params={"coinId": coin_id},
                )
                if resp.status_code != 200:
                    continue

                social = resp.json().get("Data", {})

                # Reddit subscribers + active users
                reddit = social.get("Reddit", {})
                reddit_subs = reddit.get("subscribers", 0)
                reddit_active = reddit.get("active_users", 0)
                reddit_posts_day = reddit.get("posts_per_day", 0)

                # Twitter followers
                twitter = social.get("Twitter", {})
                twitter_followers = twitter.get("followers", 0)

                # CryptoCompare community
                cc = social.get("CryptoCompare", {})
                cc_followers = cc.get("Followers", 0)
                cc_posts = cc.get("Posts", 0)

                # General sentiment from code repository activity
                code = social.get("CodeRepository", {})
                code_stars = 0
                code_forks = 0
                if code and isinstance(code.get("List", []), list):
                    for repo in code.get("List", []):
                        code_stars += repo.get("stars", 0)
                        code_forks += repo.get("forks", 0)

                # Composite social score: normalized 0-100
                # Higher Reddit activity + Twitter = more attention = potential volatility
                social_activity = (
                    min(reddit_active, 10000) / 100 +  # 0-100
                    min(reddit_posts_day, 500) / 5 +    # 0-100
                    min(twitter_followers, 10000000) / 100000  # 0-100
                ) / 3

                self._social_data[symbol] = {
                    "reddit_active": reddit_active,
                    "reddit_posts_day": reddit_posts_day,
                    "twitter_followers": twitter_followers,
                    "social_score": round(social_activity, 1),
                }

                if reddit_active > 100 or reddit_posts_day > 50:
                    logger.info(
                        f"SOCIAL: {symbol} reddit_active={reddit_active} "
                        f"posts/day={reddit_posts_day} score={social_activity:.1f}"
                    )
            except Exception as e:
                logger.debug(f"Social fetch failed for {symbol}: {e}")

    def get_social_signal(self, symbol: str) -> dict:
        """Returns social sentiment signal.

        High social activity during price move = confirmation.
        Extreme social buzz = potential top/bottom (contrarian).

        Returns:
            {"score": float, "signal": str, "boost": float}
        """
        data = self._social_data.get(symbol, {})
        if not data:
            return {"score": 0, "signal": "NEUTRAL", "boost": 0.0}

        score = data.get("social_score", 0)

        # Very high social = contrarian (crowd is usually wrong at extremes)
        # Moderate social = confirmation boost
        if score > 80:
            signal = "EXTREME_BUZZ"
            boost = -0.01  # Contrarian: too much hype = potential top
        elif score > 40:
            signal = "HIGH_ACTIVITY"
            boost = 0.005  # Moderate buzz = good
        else:
            signal = "NEUTRAL"
            boost = 0.0

        return {
            "score": score,
            "signal": signal,
            "boost": round(boost, 4),
        }

    # ═══════════════════════════════════════════════════════════════════════
    # MASTER REFRESH — call all sources
    # ═══════════════════════════════════════════════════════════════════════

    async def refresh(self, symbols: set[str]) -> None:
        """Refresh all enhanced signal sources in parallel."""
        tasks = [
            self.fetch_multi_exchange(symbols),
            self.fetch_options_data(symbols),
            self.fetch_whale_data(symbols),
            self.fetch_social_sentiment(symbols),
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    def get_all_signals(self, symbol: str) -> dict:
        """Get all enhanced signals for a symbol in one call."""
        return {
            "multi_exchange": self.get_multi_exchange_signal(symbol),
            "options": self.get_options_signal(symbol),
            "whale": self.get_whale_signal(symbol),
            "social": self.get_social_signal(symbol),
        }
