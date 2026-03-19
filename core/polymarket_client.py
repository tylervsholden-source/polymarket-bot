from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
import httpx
from loguru import logger

# Immutable trade journal — append-only, never edited/deleted
TRADE_JOURNAL_FILE = Path("data/trade_journal.jsonl")

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet


class PolymarketClient:
    def __init__(self):
        self.private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
        self.wallet_address = os.getenv("POLYMARKET_WALLET_ADDRESS")
        self.session = httpx.AsyncClient(timeout=20)
        self._clob: Any = None

        if self.private_key and self.wallet_address:
            self._init_clob()

    def _init_clob(self):
        """py-clob-client'ı başlat ve kimlik doğrula."""
        try:
            from py_clob_client.client import ClobClient
            self._clob = ClobClient(
                host=CLOB_HOST,
                key=self.private_key,
                chain_id=CHAIN_ID,
                signature_type=1,       # POLY_PROXY (Gmail/social login)
                funder=self.wallet_address,
            )
            self._clob.set_api_creds(self._clob.create_or_derive_api_creds())
            logger.info("CLOB client başlatıldı (gerçek mod).")
            self._ensure_allowance()
        except ImportError:
            logger.warning("py-clob-client kurulu değil. `pip install py-clob-client`")
            self._clob = None
        except Exception as e:
            logger.error(f"CLOB client başlatılamadı: {e}")
            self._clob = None

    # USDC 6 ondalık: API micro-USDC döner → gerçek değer için /10^6
    _USDC_DECIMALS = 10 ** 6

    def _ensure_allowance(self):
        """USDC (COLLATERAL) allowance kontrol et, yetersizse approve yap.

        NOT: CTF/CONDITIONAL allowance token_id olmadan sorgulanamaz (erc1155).
        Token bazlı allowance order anında place_order icinde ayrica kontrol edilir.
        """
        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            result = self._clob.get_balance_allowance(params)
            raw_balance   = float(result.get("balance", 0) or 0)
            raw_allowance = float(result.get("allowance", 0) or 0)
            balance   = raw_balance   / self._USDC_DECIMALS
            allowance = raw_allowance / self._USDC_DECIMALS
            logger.info(f"USDC bakiye: ${balance:.4f} | Allowance: ${allowance:.4f}")
            if allowance < 0.01:
                logger.info("USDC allowance yetersiz, guncelleniyor...")
                self._clob.update_balance_allowance(params)
                logger.success("USDC allowance guncellendi.")
        except Exception as e:
            logger.warning(f"Allowance kontrolu basarisiz: {e}")

    def get_real_balance(self) -> float:
        """Polymarket CLOB'dan gerçek USDC bakiyesini döner ($)."""
        if not self._clob:
            return 0.0
        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            result = self._clob.get_balance_allowance(params)
            raw = float(result.get("balance", 0) or 0)
            balance = raw / self._USDC_DECIMALS
            logger.info(f"Gerçek USDC bakiye: ${balance:.4f} (ham: {raw})")
            return balance
        except Exception as e:
            logger.warning(f"Bakiye alınamadı: {e}")
            return 0.0

    # ------------------------------------------------------------------ #
    # Market Data (Gamma API — auth gerekmez)
    # ------------------------------------------------------------------ #

    def _normalize_markets(self, markets: list[dict], min_volume: float) -> list[dict]:
        """Raw market listesini normalize et ve filtrele."""
        import json as _json
        filtered = []
        seen_ids = set()
        for m in markets:
            cid = m.get("conditionId") or m.get("condition_id", "")
            if cid in seen_ids:
                continue
            volume = float(m.get("volume", 0) or 0)
            best_ask = m.get("bestAsk") or m.get("best_ask")
            token_ids = m.get("clobTokenIds") or []
            if isinstance(token_ids, str):
                try:
                    token_ids = _json.loads(token_ids)
                except Exception:
                    token_ids = []
            if volume >= min_volume and best_ask is not None and token_ids:
                m["best_ask"] = float(best_ask)
                m["best_bid"] = float(m.get("bestBid") or m.get("best_bid") or 0)
                m["yes_token_id"] = token_ids[0] if len(token_ids) > 0 else None
                m["no_token_id"] = token_ids[1] if len(token_ids) > 1 else None
                m["condition_id"] = cid
                m["end_date_iso"] = m.get("endDateIso") or m.get("end_date_iso", "")
                filtered.append(m)
                seen_ids.add(cid)
        return filtered

    async def get_active_markets(self, min_volume: float = 0) -> list[dict]:
        """
        Aktif marketleri döner. Adaptif pagination:
        - endDate ascending sıralamasıyla çek
        - Future market bulmaya devam et; iki ardışık boş sayfa gelince dur
        - Crypto up/down marketleri zaman içinde offset'i kayar — sabit liste kırılgan
        """
        from datetime import datetime, timezone
        import asyncio as _asyncio

        all_markets: list[dict] = []
        params_base = {
            "active": "true",
            "closed": "false",
            "limit": 500,
            "order": "endDate",
            "ascending": "true",
        }

        CRYPTO_KW = [
            "bitcoin up or down", "ethereum up or down", "solana up or down",
            "btc up or down", "eth up or down", "sol up or down", "xrp up or down",
            "dogecoin up or down", "doge up or down", "bnb up or down",
            "hyperliquid up or down", "hype up or down",
        ]
        now = datetime.now(timezone.utc)

        async def _fetch_offset(offset: int) -> list:
            try:
                resp = await self.session.get(
                    f"{GAMMA_API}/markets",
                    params={**params_base, "offset": offset},
                    timeout=15,
                )
                if resp.status_code == 200:
                    return resp.json()
            except Exception as e:
                logger.debug(f"Market fetch offset={offset} hata: {e}")
            return []

        # Adaptif tarama: 500'erli adımlarla, future crypto up/down bulunamayana dek
        # Max 10_000 market tara (güvenlik limiti)
        step = 500
        max_offset = 10_000
        empty_streak = 0  # art arda future-crypto-free sayfa sayısı
        seen_ids: set = set()

        offsets_to_scan = list(range(0, max_offset, step))

        # Paralel 4'lü batch'ler halinde tara — hız + güvenlik dengesi
        batch_size = 4
        for i in range(0, len(offsets_to_scan), batch_size):
            batch_offsets = offsets_to_scan[i:i + batch_size]
            results = await _asyncio.gather(*[_fetch_offset(o) for o in batch_offsets])

            found_future_in_batch = False
            for data in results:
                if not data:
                    continue
                for m in data:
                    end_str = m.get("endDate", "") or m.get("endDateIso", "")
                    if not end_str:
                        continue
                    try:
                        s = str(end_str).replace("Z", "+00:00")
                        if len(s) == 10:
                            s += "T23:59:00+00:00"
                        end_dt = datetime.fromisoformat(s)
                        if end_dt.tzinfo is None:
                            end_dt = end_dt.replace(tzinfo=timezone.utc)
                        if end_dt > now:
                            q = m.get("question", "").lower()
                            if any(kw in q for kw in CRYPTO_KW):
                                found_future_in_batch = True
                        all_markets.append(m)
                    except Exception:
                        all_markets.append(m)

            if found_future_in_batch:
                empty_streak = 0
            else:
                empty_streak += 1
                # 3 ardışık batch (1500 market) future crypto yoksa dur
                if empty_streak >= 3 and i > batch_size * 3:
                    logger.debug(
                        f"Adaptif tarama bitti: offset={batch_offsets[-1]}, "
                        f"toplam={len(all_markets)}"
                    )
                    break

        # Sadece gelecekteki marketleri al — endDate (full timestamp) kullan
        now = datetime.now(timezone.utc)
        future_markets = []
        seen_ids: set = set()
        for m in all_markets:
            cid = m.get("conditionId") or m.get("condition_id", "")
            if cid in seen_ids:
                continue
            # endDate has full timestamp (e.g. "2026-03-14T08:00:00Z")
            # endDateIso is date-only ("2026-03-14") — don't use for time comparison
            end_str = m.get("endDate", "")
            if not end_str:
                end_str = m.get("endDateIso", "")
            if not end_str:
                continue
            try:
                s = str(end_str).replace("Z", "+00:00")
                if len(s) == 10:
                    # date-only fallback: assume end of day
                    s += "T23:59:00+00:00"
                end_dt = datetime.fromisoformat(s)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                if end_dt > now:
                    future_markets.append(m)
                    seen_ids.add(cid)
            except Exception:
                pass

        logger.info(f"Toplam çekilen: {len(all_markets)} | Gelecekteki: {len(future_markets)}")
        return self._normalize_markets(future_markets, min_volume)

    async def get_market(self, condition_id: str, gamma_id: str | None = None) -> dict | None:
        try:
            # Numeric gamma_id varsa direkt path ile çek (daha güvenilir)
            if gamma_id:
                resp = await self.session.get(f"{GAMMA_API}/markets/{gamma_id}")
            else:
                resp = await self.session.get(f"{GAMMA_API}/markets", params={"conditionId": condition_id})
            resp.raise_for_status()
            raw = resp.json()
            data_list = raw if isinstance(raw, list) else [raw]
            if not data_list:
                return None
            # Match correct market by conditionId — Gamma API may return
            # wrong market if conditionId param is ignored/unrecognized
            data = None
            for candidate in data_list:
                cid = candidate.get("conditionId") or candidate.get("condition_id", "")
                if cid == condition_id:
                    data = candidate
                    break
            if data is None:
                # No match — API returned unrelated market(s)
                logger.debug(f"get_market: conditionId mismatch for {condition_id[:20]}...")
                return None
            # Normalize
            data["best_ask"] = float(data.get("bestAsk") or data.get("best_ask") or 0)
            data["best_bid"] = float(data.get("bestBid") or data.get("best_bid") or 0)
            data["condition_id"] = data.get("conditionId") or data.get("condition_id", "")
            data["end_date_iso"] = data.get("endDateIso") or data.get("end_date_iso", "")
            return data
        except Exception as e:
            logger.error(f"Market alınamadı ({condition_id}): {e}")
            return None

    # ------------------------------------------------------------------ #
    # Order Placement (CLOB API — imza gerekir)
    # ------------------------------------------------------------------ #

    def _journal_trade(self, entry: dict) -> None:
        """Append-only trade journal. NEVER loses a record, even on crash."""
        entry["ts"] = time.time()
        entry["iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            TRADE_JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TRADE_JOURNAL_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"TRADE JOURNAL WRITE FAILED: {e} | entry={entry}")

    async def place_order(
        self,
        market_id: str,
        outcome: str,
        amount: float,
        price: float,
        token_id: str | None = None,
        question: str = "",
    ) -> dict | None:
        """
        Polymarket CLOB'a limit emir gönderir.
        token_id: market'in YES (index 0) veya NO (index 1) token ID'si.

        CRITICAL: Every order attempt is journaled to data/trade_journal.jsonl
        BEFORE submission. This prevents silent money loss.
        """
        # ── JOURNAL: Record intent BEFORE sending to CLOB ──
        journal_entry = {
            "action": "ORDER_ATTEMPT",
            "market_id": market_id,
            "outcome": outcome,
            "amount": amount,
            "price": price,
            "token_id": (token_id or "")[:20],
            "question": question[:80],
            "is_live": self._clob is not None,
        }

        if not self._clob:
            journal_entry["result"] = "SIMULATED"
            self._journal_trade(journal_entry)
            return self._simulate(market_id, outcome, amount, price)

        if not token_id:
            logger.error("token_id gerekli (clobTokenIds[0/1]).")
            journal_entry["result"] = "REJECTED_NO_TOKEN_ID"
            self._journal_trade(journal_entry)
            return None

        # Journal the attempt BEFORE calling CLOB
        journal_entry["result"] = "PENDING"
        self._journal_trade(journal_entry)

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            side = BUY

            from py_clob_client.order_builder.builder import ROUNDING_CONFIG
            from py_clob_client.clob_types import RoundConfig
            # Patch ALL tick sizes — amount=4 prevents "invalid amounts" errors.
            # Without this, py-clob-client defaults to amount=2 which truncates
            # maker_amount and causes CLOB rejection (e.g. "should be 3.9912 but got 3.99").
            for _ts, _pd in [("0.1", 1), ("0.01", 2), ("0.001", 3), ("0.0001", 4)]:
                ROUNDING_CONFIG[_ts] = RoundConfig(price=_pd, size=2, amount=4)

            # GTC with slight price bump for fill priority.
            # +0.01 instead of +0.02 — audit found the 0.02 overpay ate 25% of edge.
            # Edge model accounts for 0.01 total cost. Must match.
            import asyncio
            import math

            price = round(min(price + 0.01, 0.99), 2)

            # Size (taker_amount) max 2 decimals per CLOB API.
            # maker_amount (USDC) max 4 decimals.
            size = math.floor(amount / price * 100) / 100

            # CLOB requires notional (size * price) >= $1.00
            while size * price < 1.0 and price > 0:
                size = round(size + 0.01, 2)
            maker_amount = math.floor(size * price * 10000) / 10000
            if abs(maker_amount - amount) > 0.01:
                logger.info(f"CLOB amount adjust: target=${amount} actual=${maker_amount:.4f} size={size:.4f}")

            logger.info(
                f"CLOB GTC order: price={price} size={size:.4f} "
                f"amount={amount} notional=${size*price:.4f}"
            )

            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
            )
            signed = self._clob.create_order(order_args)
            response = self._clob.post_order(signed, OrderType.GTC)

            if not response or not isinstance(response, dict):
                logger.error(f"GTC emir reddedildi (response={response})")
                self._journal_trade({
                    "action": "ORDER_RESULT", "market_id": market_id,
                    "outcome": outcome, "amount": amount, "price": price,
                    "result": "GTC_REJECTED", "response": str(response)[:200],
                    "question": question[:80],
                })
                return None

            order_id = response.get("orderID") or response.get("id", "")
            status = response.get("status", "UNKNOWN")

            if not order_id:
                logger.error("CLOB bos order_id dondu — emir kayip olabilir.")
                self._journal_trade({
                    "action": "ORDER_RESULT", "market_id": market_id,
                    "outcome": outcome, "amount": amount, "price": price,
                    "result": "EMPTY_ORDER_ID", "response": str(response)[:200],
                    "question": question[:80],
                })
                return None

            # GTC: emir hemen dolmayabilir — 30sn bekle, dolmamışsa iptal et
            if status != "matched":
                logger.info(f"GTC emir gönderildi ({order_id}), fill bekleniyor (max 30sn)...")
                filled = False
                for _wait in range(6):  # 6 × 5s = 30s
                    await asyncio.sleep(5)
                    try:
                        order_info = self._clob.get_order(order_id)
                        if order_info and isinstance(order_info, dict):
                            cur_status = order_info.get("status", "")
                            size_matched = float(order_info.get("size_matched", 0) or 0)
                            logger.info(f"GTC poll: status={cur_status} matched={size_matched:.2f}/{size:.2f}")
                            if cur_status == "matched" or size_matched >= size * 0.95:
                                status = "matched"
                                filled = True
                                break
                    except Exception as poll_err:
                        logger.debug(f"GTC poll hatası: {poll_err}")

                if not filled:
                    # 30sn doldu, dolmadı — iptal et
                    logger.warning(f"GTC emir 30sn içinde dolmadı — iptal ediliyor: {order_id}")
                    try:
                        self._clob.cancel(order_id)
                        logger.info(f"GTC emir iptal edildi: {order_id}")
                    except Exception as cancel_err:
                        logger.error(f"GTC iptal hatası: {cancel_err}")
                    self._journal_trade({
                        "action": "ORDER_RESULT", "market_id": market_id,
                        "outcome": outcome, "amount": amount, "price": price,
                        "result": "GTC_TIMEOUT_CANCELLED", "order_id": order_id,
                        "question": question[:80],
                    })
                    return None

            logger.success(f"Emir dolduruldu: {order_id} status={status}")

            # ── JOURNAL: Record successful order ──
            self._journal_trade({
                "action": "ORDER_RESULT", "market_id": market_id,
                "outcome": outcome, "amount": amount, "price": price,
                "result": "ACCEPTED", "order_id": order_id,
                "status": status, "question": question[:80],
            })

            # Return actual cost (size may have been adjusted for min notional)
            actual_amount = round(size * price, 4)
            return {
                "order_id": order_id,
                "market_id": market_id,
                "outcome": outcome,
                "amount": actual_amount,
                "price": price,
                "status": status,
            }
        except Exception as e:
            logger.error(f"Emir verilemedi: {e}")
            self._journal_trade({
                "action": "ORDER_RESULT", "market_id": market_id,
                "outcome": outcome, "amount": amount, "price": price,
                "result": "EXCEPTION", "error": str(e)[:200],
                "question": question[:80],
            })
            return None

    def get_orderbook(self, token_id: str) -> dict | None:
        """CLOB orderbook'tan token fiyatlarını çeker.

        Returns: {"best_ask": float, "best_bid": float} or None
        """
        if not self._clob or not token_id:
            return None
        try:
            book = self._clob.get_order_book(token_id)
            if not book:
                return None
            asks = book.asks if hasattr(book, "asks") else []
            bids = book.bids if hasattr(book, "bids") else []
            # CLOB API: asks descending, bids descending
            # asks[0]=en pahalı, asks[-1]=en ucuz (best ask)
            # bids[0]=en yüksek (best bid), bids[-1]=en düşük
            best_ask = min(float(a.price) for a in asks) if asks else 0.0
            best_bid = max(float(b.price) for b in bids) if bids else 0.0
            return {"best_ask": best_ask, "best_bid": best_bid}
        except Exception as e:
            logger.debug(f"Orderbook alınamadı ({token_id[:20]}...): {e}")
            return None

    async def get_order_status(self, order_id: str) -> dict | None:
        if not self._clob:
            return None
        try:
            return self._clob.get_order(order_id)
        except Exception as e:
            logger.error(f"Emir durumu alınamadı: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Simülasyon (API key yokken)
    # ------------------------------------------------------------------ #

    def _simulate(self, market_id: str, outcome: str, amount: float, price: float) -> dict:
        logger.warning("Simülasyon modu — gerçek emir gönderilmedi.")
        return {
            "order_id": f"SIM-{market_id}",
            "market_id": market_id,
            "outcome": outcome,
            "amount": amount,
            "price": price,
            "status": "SIMULATED",
        }

    @property
    def is_live(self) -> bool:
        return self._clob is not None
