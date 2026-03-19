from __future__ import annotations

import os
from typing import Any
import httpx
from loguru import logger

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
            data = data_list[0]
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

    async def place_order(
        self,
        market_id: str,
        outcome: str,
        amount: float,
        price: float,
        token_id: str | None = None,
    ) -> dict | None:
        """
        Polymarket CLOB'a limit emir gönderir.
        token_id: market'in YES (index 0) veya NO (index 1) token ID'si.
        """
        if not self._clob:
            return self._simulate(market_id, outcome, amount, price)

        if not token_id:
            logger.error("token_id gerekli (clobTokenIds[0/1]).")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            side = BUY  # Her zaman BUY — caller doğru token_id'yi geçer
            size = amount / price  # Harcanan USDC → share sayısı

            order_args = OrderArgs(
                token_id=token_id,
                price=round(price, 4),
                size=round(size, 2),
                side=side,
            )
            signed = self._clob.create_order(order_args)
            response = self._clob.post_order(signed, OrderType.GTC)

            order_id = response.get("orderID") or response.get("id", "unknown")
            logger.success(f"Emir kabul edildi: {order_id}")

            return {
                "order_id": order_id,
                "market_id": market_id,
                "outcome": outcome,
                "amount": amount,
                "price": price,
                "status": response.get("status", "LIVE"),
            }
        except Exception as e:
            logger.error(f"Emir verilemedi: {e}")
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
