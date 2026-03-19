import os
import json
from datetime import date, datetime, timezone
from pathlib import Path
from loguru import logger


DATA_FILE = Path("data/positions.json")


class PositionManager:
    def __init__(self):
        self.initial_capital = float(os.getenv("INITIAL_CAPITAL", 1000))
        self.max_position_pct = float(os.getenv("MAX_POSITION_PCT", 0.20))
        DATA_FILE.parent.mkdir(exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict:
        if DATA_FILE.exists():
            with open(DATA_FILE) as f:
                return json.load(f)
        return {
            "capital": self.initial_capital,
            "positions": {},
            "closed": [],
            "daily": {"date": str(date.today()), "pnl": 0},
        }

    def _save(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.data, f, indent=2)

    def available_capital(self) -> float:
        """Free cash = total capital minus cost of open positions."""
        locked = sum(p["amount"] for p in self.data["positions"].values())
        return max(0, self.data["capital"] - locked)

    def open_position_count(self) -> int:
        return len(self.data["positions"])

    def has_position(self, market_id: str) -> bool:
        return market_id in self.data["positions"]

    def add_position(self, market_id: str, order: dict, question: str):
        self.data["positions"][market_id] = {
            "order_id": order["order_id"],
            "question": question,
            "outcome": order.get("outcome", "YES"),
            "amount": order["amount"],
            "entry_price": order["price"],
            "status": order["status"],
        }
        self._save()
        logger.info(f"Pozisyon eklendi: {question[:50]}")

    def daily_loss_exceeded(self, threshold: float) -> bool:
        today = str(date.today())
        if self.data["daily"]["date"] != today:
            self.data["daily"] = {"date": today, "pnl": 0}
            self._save()
        loss_pct = abs(min(0, self.data["daily"]["pnl"])) / self.initial_capital
        return loss_pct >= threshold

    async def update_positions(self, client):
        """Açık pozisyonların güncel fiyatlarını çeker, kapananları kapatır.

        Emir dolum durumunu CLOB'dan kontrol eder:
        - Dolmamış (LIVE) emir + market expired → NEUTRAL (USDC iade)
        - Dolmuş (MATCHED) emir + market expired → resolution'a göre WIN/LOSS
        """
        now = datetime.now(timezone.utc)
        for market_id in list(self.data["positions"]):
            pos = self.data["positions"][market_id]
            market = await client.get_market(market_id, gamma_id=pos.get("gamma_id"))

            # Market resolved/kapalı mı?
            market_expired = False
            end_date_str = (market or {}).get("end_date_iso") or ""
            if end_date_str:
                try:
                    s = str(end_date_str).replace("Z", "+00:00")
                    if len(s) == 10:
                        s += "T23:59:00+00:00"
                    end_dt = datetime.fromisoformat(s)
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                    market_expired = end_dt < now
                except Exception:
                    pass
            if market and (market.get("resolved") or market.get("closed")):
                market_expired = True

            # ── Emir dolum durumunu kontrol et ──
            order_filled = await self._check_order_filled(client, pos)

            if market is None:
                if order_filled:
                    # Token var ama market API'den silindi — LOSS olarak kaydet
                    logger.info(f"Market silinmiş, emir dolmuş → LOSS: {pos['question'][:50]}")
                    self._close_position(market_id, 0.0)
                else:
                    # Emir dolmadı, USDC iade edildi → NEUTRAL
                    logger.info(f"Market silinmiş, emir dolmamış → NEUTRAL: {pos['question'][:50]}")
                    self._close_position_neutral(market_id)
                continue

            yes_ask = float(market.get("best_ask", 0) or 0) or (1.0 - float(market.get("best_bid", 0) or 0))
            outcome = pos.get("outcome", "YES").upper()

            # ── Market resolved → resolution outcome'dan close price belirle ──
            is_resolved = market.get("resolved") or market.get("closed") or market_expired
            if is_resolved:
                if not order_filled:
                    # Emir dolmadı — USDC iade edildi
                    logger.info(f"Emir dolmadı (NEUTRAL): {pos['question'][:50]}")
                    self._close_position_neutral(market_id)
                    continue

                # Emir doldu — resolution'a göre WIN/LOSS
                resolution = self._get_resolution_outcome(market)
                if resolution == "YES":
                    close_price = 1.0 if outcome == "YES" else 0.0
                elif resolution == "NO":
                    close_price = 0.0 if outcome == "YES" else 1.0
                else:
                    # Resolution bilinmiyor — gerçek orderbook'tan oku (fallback)
                    if outcome == "NO":
                        no_tid = (market or {}).get("no_token_id")
                        no_book = client.get_orderbook(no_tid) if no_tid else None
                        if no_book and no_book["best_bid"] > 0:
                            close_price = no_book["best_bid"]
                        else:
                            close_price = round(1.0 - yes_ask, 4) if yes_ask > 0 else 0.0
                    else:
                        close_price = float(market.get("best_bid", 0) or 0)
                self._close_position(market_id, close_price)
                continue

            # Token fiyatı: YES için YES bid, NO için gerçek NO orderbook
            if outcome == "NO":
                # Gerçek NO orderbook fiyatını çek
                no_tid = (market or {}).get("no_token_id")
                no_book = client.get_orderbook(no_tid) if no_tid else None
                if no_book and no_book["best_bid"] > 0:
                    current_price = no_book["best_bid"]
                else:
                    current_price = round(1.0 - yes_ask, 4) if yes_ask > 0 else pos["entry_price"]
            else:
                current_price = float(market.get("best_bid", pos["entry_price"]) or pos["entry_price"])

            amount = pos["amount"]
            entry = pos["entry_price"]
            shares = amount / entry if entry > 0 else 0
            current_value = shares * current_price
            pnl = current_value - amount

            pos["current_price"] = round(current_price, 4)
            pos["current_value"] = round(current_value, 2)
            pos["unrealized_pnl"] = round(pnl, 2)

        self._save()

    @staticmethod
    async def _check_order_filled(client, pos: dict) -> bool:
        """CLOB'dan emir dolum durumunu kontrol et.

        Returns True if order was matched/filled, False if still live/cancelled.
        """
        # İlk status zaten MATCHED ise kesinlikle dolmuş
        status = (pos.get("status") or "").upper()
        if status in ("MATCHED", "FILLED"):
            return True

        # CLOB'dan güncel durumu sor
        order_id = pos.get("order_id", "")
        if not order_id or order_id.startswith("SIM-"):
            return False

        try:
            order_data = await client.get_order_status(order_id)
            if order_data:
                clob_status = (order_data.get("status") or "").upper()
                if clob_status in ("MATCHED", "FILLED"):
                    pos["status"] = "matched"
                    return True
                # size_matched > 0 ise kısmen dolmuş
                size_matched = float(order_data.get("size_matched", 0) or 0)
                if size_matched > 0:
                    pos["status"] = "matched"
                    return True
        except Exception as e:
            logger.debug(f"Order status kontrol hatası: {e}")

        return False

    @staticmethod
    def _get_resolution_outcome(market: dict) -> str | None:
        """Market resolution sonucunu döner: 'YES', 'NO', veya None (bilinmiyor).

        Gamma API resolved market'lerde şu alanları döndürebilir:
        - outcome: "Yes"/"No"/"Up"/"Down"
        - resolution: "Yes"/"No"
        - resolutionSource / winner
        """
        for field in ("outcome", "resolution", "winner"):
            val = (market.get(field) or "").strip().lower()
            if val in ("yes", "up", "1", "true"):
                return "YES"
            if val in ("no", "down", "0", "false"):
                return "NO"
        # best_ask yaklaşımı: resolved markette ask ~1.0 ise YES kazandı
        ask = float(market.get("best_ask", 0) or 0)
        bid = float(market.get("best_bid", 0) or 0)
        if ask >= 0.95 or bid >= 0.95:
            return "YES"
        if ask <= 0.05 and bid <= 0.05:
            return "NO"
        return None

    def _close_position_neutral(self, market_id: str):
        """Dolmamış emir — USDC iade edildi, PnL = 0."""
        pos = self.data["positions"].pop(market_id)
        pos["close_price"] = pos["entry_price"]
        pos["pnl"] = 0.0
        pos["payout"] = pos["amount"]
        pos["result"] = "NEUTRAL"

        existing_ids = {c.get("order_id") for c in self.data["closed"]}
        if pos.get("order_id") in existing_ids:
            return

        # Capital değişmez — USDC zaten iade edildi
        self.data["closed"].append(pos)
        logger.info(
            f"Pozisyon kapatıldı (dolmadı): {pos['question'][:40]} | NEUTRAL"
        )

    def _close_position(self, market_id: str, token_close_price: float):
        """
        token_close_price: kapanış anındaki bu tokenin fiyatı
          - YES pozisyon için: YES fiyatı (0.0 = loss, 1.0 = win)
          - NO pozisyon için: NO fiyatı (0.0 = loss, 1.0 = win)
        """
        pos = self.data["positions"].pop(market_id)
        amount = pos["amount"]
        entry = pos["entry_price"]
        shares = amount / entry if entry > 0 else 0
        payout = shares * token_close_price
        pnl = payout - amount

        pos["close_price"] = round(token_close_price, 4)
        pos["pnl"] = round(pnl, 2)
        pos["payout"] = round(payout, 2)
        pos["result"] = "WIN" if pnl > 0 else ("NEUTRAL" if pnl == 0 else "LOSS")

        # Duplicate guard: aynı order_id zaten closed'daysa capital'e dokunma, append etme
        existing_ids = {c.get("order_id") for c in self.data["closed"]}
        if pos.get("order_id") in existing_ids:
            logger.debug(f"Duplicate closed trade atlandı: {pos.get('order_id')}")
            return

        # Sermayeyi sadece PnL kadar güncelle — principal zaten locked olarak sayılıyordu
        self.data["capital"] += pnl
        self.data["daily"]["pnl"] += pnl
        self.data["closed"].append(pos)
        logger.info(
            f"Pozisyon kapatıldı: {pos['question'][:40]} | "
            f"{pos['result']} | PnL: ${pnl:+.2f}"
        )

    def print_status(self):
        capital = self.data["capital"]
        target = float(os.getenv("INITIAL_CAPITAL", 1000)) * 3
        open_pos = self.open_position_count()
        unrealized = sum(p.get("unrealized_pnl", 0) for p in self.data["positions"].values())
        total_pnl = capital - self.initial_capital + unrealized

        logger.info("=" * 50)
        logger.info(f"Sermaye    : ${capital:.2f}")
        logger.info(f"Unrealized : ${unrealized:.2f}")
        logger.info(f"Toplam PnL : ${total_pnl:.2f} ({total_pnl/self.initial_capital*100:.1f}%)")
        logger.info(f"Hedef      : ${target:.0f} ({capital/target*100:.1f}% tamamlandı)")
        logger.info(f"Açık Poz   : {open_pos}")
        logger.info("=" * 50)
