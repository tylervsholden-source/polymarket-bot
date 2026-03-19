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
        """Açık pozisyonların güncel fiyatlarını çeker, kapananları kapatır."""
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

            if market is None:
                logger.info(f"Market bulunamadı (resolved), kapatılıyor: {pos['question'][:50]}")
                self._close_position(market_id, 0.0)
                continue

            yes_ask = float(market.get("best_ask", 0) or 0) or (1.0 - float(market.get("best_bid", 0) or 0))
            outcome = pos.get("outcome", "YES").upper()

            # Token fiyatı: YES için YES bid, NO için 1 - YES ask
            if outcome == "NO":
                yes_bid = float(market.get("best_bid", 0) or 0)
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

            if not market.get("active", True) or market_expired:
                self._close_position(market_id, current_price)

        self._save()

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
