import os
import json
import re
import time as _time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import aiohttp
from loguru import logger

# Spot-based resolution: coin adından Bitstamp pair'e
_COIN_TO_PAIR = {
    "bitcoin": "btcusd", "btc": "btcusd",
    "ethereum": "ethusd", "eth": "ethusd",
    "solana": "solusd", "sol": "solusd",
    "xrp": "xrpusd",
    "dogecoin": "dogeusd", "doge": "dogeusd",
    "bnb": "bnbusd",
    "hyperliquid": "hypeusd", "hype": "hypeusd",
}


DATA_FILE = Path("data/positions.json")
LOCK_FILE = Path("data/positions.lock")


@contextmanager
def _file_lock(lock_path: Path = LOCK_FILE, timeout: float = 5.0):
    """Cross-platform file lock using atomic file creation."""
    lock_path.parent.mkdir(exist_ok=True)
    deadline = _time.monotonic() + timeout
    fd = None
    try:
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                if _time.monotonic() > deadline:
                    # Stale lock — force acquire
                    try:
                        lock_path.unlink()
                    except OSError:
                        pass
                    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    break
                _time.sleep(0.05)
        yield
    finally:
        if fd is not None:
            os.close(fd)  # type: ignore[arg-type]
        try:
            lock_path.unlink()
        except OSError:
            pass


class PositionManager:
    def __init__(self):
        self.initial_capital = float(os.getenv("INITIAL_CAPITAL", 1000))
        self.max_position_pct = float(os.getenv("MAX_POSITION_PCT", 0.20))
        DATA_FILE.parent.mkdir(exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict:
        with _file_lock():
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
        with _file_lock():
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
            "token_id": order.get("token_id", ""),
        }
        self._save()
        logger.info(f"Pozisyon eklendi: {question[:50]}")

    def daily_loss_exceeded(self, threshold: float) -> bool:
        today = str(datetime.now(timezone.utc).date())
        if self.data["daily"]["date"] != today:
            self.data["daily"] = {"date": today, "pnl": 0}
            self._save()
        if self.initial_capital <= 0:
            return False
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

            # Question'dan zaman parse ederek expired kontrolü
            # Polymarket API bazen geç güncelleniyor, ama biz saati biliyoruz
            if not market_expired:
                question = pos.get("question", "")
                time_match = re.search(
                    r'(\d{1,2}):(\d{2})(AM|PM)\s*-\s*(\d{1,2}):(\d{2})(AM|PM)\s*ET',
                    question, re.IGNORECASE
                )
                if time_match:
                    end_h = int(time_match.group(4))
                    end_m = int(time_match.group(5))
                    end_ampm = time_match.group(6).upper()
                    if end_ampm == "PM" and end_h != 12:
                        end_h += 12
                    elif end_ampm == "AM" and end_h == 12:
                        end_h = 0
                    # Use proper timezone (handles EDT/EST automatically)
                    from zoneinfo import ZoneInfo
                    now_et = datetime.now(ZoneInfo("America/New_York"))
                    market_end_min = end_h * 60 + end_m
                    now_min = now_et.hour * 60 + now_et.minute
                    if now_min >= market_end_min + 1:  # 1 dakika margin
                        market_expired = True
                        logger.info(f"TIME_EXPIRED: {question[:50]} (end={end_h}:{end_m:02d} ET, now={now_et.hour}:{now_et.minute:02d} ET)")

            # ── Emir dolum durumunu kontrol et ──
            order_filled = await self._check_order_filled(client, pos)

            if market is None:
                # Gamma API conditionId lookup unreliable — use CLOB token resolution
                # Check if market end time (from question) has passed
                from zoneinfo import ZoneInfo
                question = pos.get("question", "")
                end_m = re.search(
                    r'(\d{1,2}):(\d{2})\s*(AM|PM)\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)',
                    question, re.IGNORECASE,
                )
                market_ended = False
                if end_m:
                    _, _, _, h2, m2, ap2 = end_m.groups()
                    end_hour = (int(h2) % 12) + (12 if ap2.upper() == "PM" else 0)
                    end_min = int(m2)
                    now_et = datetime.now(ZoneInfo("America/New_York"))
                    market_end_mins = end_hour * 60 + end_min
                    now_mins = now_et.hour * 60 + now_et.minute
                    # Market ended if current time > end time + 1 min buffer
                    market_ended = (now_mins - market_end_mins) >= 1

                if not market_ended:
                    # Market still active — keep position open, check via CLOB token
                    token_id = pos.get("token_id", "")
                    if token_id:
                        book = client.get_orderbook(token_id)
                        if book:
                            bid = float(book.get("best_bid", 0) or 0)
                            pos["current_price"] = round(bid, 4)
                            shares = pos["amount"] / pos["entry_price"] if pos["entry_price"] > 0 else 0
                            pos["current_value"] = round(shares * bid, 2)
                            pos["unrealized_pnl"] = round(shares * bid - pos["amount"], 2)
                    logger.debug(f"Gamma API miss — market still open: {question[:50]}")
                    continue

                # Market ended — resolve via spot price first, then CLOB fallback
                if order_filled:
                    resolution = None
                    outcome = pos.get("outcome", "YES").upper()

                    # 1) Spot-based resolution (Bitstamp) — en güvenilir
                    try:
                        spot_result = await self._spot_based_resolution(question)
                        if spot_result:
                            resolution = spot_result
                            logger.info(f"SPOT_RESOLUTION (no-gamma): {question[:50]} → {spot_result}")
                    except Exception as e:
                        logger.debug(f"Spot resolution failed (no-gamma): {e}")

                    # 2) CLOB token orderbook fallback
                    if resolution is None:
                        token_id = pos.get("token_id", "")
                        if token_id and hasattr(client, '_clob') and client._clob:
                            try:
                                book = client._clob.get_order_book(token_id)
                                bid = float(book.get("bids", [{}])[0].get("price", 0) if book.get("bids") else 0)
                                if bid > 0.85:
                                    resolution = "WIN_DIRECT"
                                elif bid < 0.15:
                                    resolution = "LOSS_DIRECT"
                            except Exception:
                                pass

                    # 3) Apply resolution
                    if resolution == "WIN_DIRECT":
                        logger.info(f"CLOB token resolution → WIN: {question[:50]}")
                        self._close_position(market_id, 1.0)
                    elif resolution == "LOSS_DIRECT":
                        logger.info(f"CLOB token resolution → LOSS: {question[:50]}")
                        self._close_position(market_id, 0.0)
                    elif resolution == "YES":
                        # UP won — YES holders win
                        close_price = 1.0 if outcome == "YES" else 0.0
                        result_str = "WIN" if close_price == 1.0 else "LOSS"
                        logger.info(f"SPOT resolved UP → {outcome} = {result_str}: {question[:50]}")
                        self._close_position(market_id, close_price)
                    elif resolution == "NO":
                        # DOWN won — NO holders win
                        close_price = 0.0 if outcome == "YES" else 1.0
                        result_str = "WIN" if close_price == 1.0 else "LOSS"
                        logger.info(f"SPOT resolved DOWN → {outcome} = {result_str}: {question[:50]}")
                        self._close_position(market_id, close_price)
                    else:
                        logger.warning(f"UNRESOLVED: {question[:50]} — spot+CLOB both failed, keeping position open")
                        # DON'T close as LOSS — keep open and retry next cycle
                else:
                    logger.info(f"Market ended, emir dolmamış → NEUTRAL: {question[:50]}")
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

                # Polymarket resolution bilinmiyorsa → Bitstamp spot ile anında karar ver
                if resolution is None:
                    question = pos.get("question", "")
                    try:
                        resolution = await self._spot_based_resolution(question)
                        if resolution:
                            logger.info(f"SPOT_RESOLUTION: {question[:50]} → {resolution} (Bitstamp)")
                    except Exception as e:
                        logger.debug(f"Spot resolution failed: {e}")

                if resolution == "YES":
                    close_price = 1.0 if outcome == "YES" else 0.0
                elif resolution == "NO":
                    close_price = 0.0 if outcome == "YES" else 1.0
                else:
                    # Her iki yöntem de başarısız — orderbook fallback
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
        Partial fill durumunda amount'u gerçek dolum tutarına günceller.
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
                size_matched = float(order_data.get("size_matched", 0) or 0)
                original_size = float(order_data.get("original_size", 0) or order_data.get("size", 0) or 0)

                if clob_status in ("MATCHED", "FILLED"):
                    pos["status"] = "MATCHED"
                    # Tam dolum — amount doğru zaten
                    return True

                if size_matched > 0:
                    # Kısmi dolum — amount'u gerçek harcanan USDC'ye güncelle
                    entry_price = pos.get("entry_price", 0)
                    if entry_price > 0 and original_size > 0:
                        fill_ratio = size_matched / original_size
                        original_amount = pos.get("amount", 0)
                        filled_amount = round(original_amount * fill_ratio, 4)
                        if filled_amount != original_amount:
                            logger.info(
                                f"Kısmi dolum: {fill_ratio:.0%} | "
                                f"${original_amount:.2f} → ${filled_amount:.2f}"
                            )
                            pos["amount"] = filled_amount
                    pos["status"] = "MATCHED"
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

    @staticmethod
    async def _spot_based_resolution(question: str) -> str | None:
        """Bitstamp spot fiyatıyla UP/DOWN kararı ver.

        "Bitcoin Up or Down - March 19, 4:45AM-5:00AM ET" gibi market adından
        coin ve zaman aralığını parse edip, o zaman penceresindeki open vs close karşılaştırır.
        Returns 'YES' (UP) or 'NO' (DOWN/FLAT) or None (belirlenemedi).
        """
        q = question.lower()

        # Coin bul
        pair = None
        for coin_name, bitstamp_pair in _COIN_TO_PAIR.items():
            if coin_name in q:
                pair = bitstamp_pair
                break
        if not pair:
            return None

        # Zaman aralığını parse et: "March 19, 4:45AM-5:00AM ET"
        time_match = re.search(
            r'(\w+ \d+),?\s+(\d{1,2}):(\d{2})(AM|PM)\s*-\s*(\d{1,2}):(\d{2})(AM|PM)\s*ET',
            question, re.IGNORECASE
        )
        if not time_match:
            return None

        # Start time ET → UTC
        start_h = int(time_match.group(2))
        start_m = int(time_match.group(3))
        start_ampm = time_match.group(4).upper()
        if start_ampm == "PM" and start_h != 12:
            start_h += 12
        elif start_ampm == "AM" and start_h == 12:
            start_h = 0
        # Proper timezone conversion (handles EDT/EST automatically)
        from zoneinfo import ZoneInfo
        _et_offset = datetime.now(ZoneInfo("America/New_York")).utcoffset()
        _et_hours = int(_et_offset.total_seconds() // 3600) if _et_offset else -4
        start_utc_h = (start_h - _et_hours) % 24

        # End time ET → UTC
        end_h = int(time_match.group(5))
        end_m = int(time_match.group(6))
        end_ampm = time_match.group(7).upper()
        if end_ampm == "PM" and end_h != 12:
            end_h += 12
        elif end_ampm == "AM" and end_h == 12:
            end_h = 0
        end_utc_h = (end_h - _et_hours) % 24

        now = datetime.now(timezone.utc)
        market_end_minutes = end_utc_h * 60 + end_m
        now_minutes = now.hour * 60 + now.minute
        if now_minutes < market_end_minutes:
            return None  # Market henüz kapanmamış

        # Market window uzunluğu (dakika)
        window_minutes = (end_utc_h * 60 + end_m) - (start_utc_h * 60 + start_m)
        if window_minutes <= 0:
            window_minutes += 24 * 60

        # Bitstamp OHLCV ile doğru zaman penceresini çek
        try:
            async with aiohttp.ClientSession() as session:
                ohlcv_url = f"https://www.bitstamp.net/api/v2/ohlc/{pair}/"
                # 60s mumlar ile kesin window hesabı
                # window_minutes * 2 kadar mum çek (yeterli kapsam)
                step = 60  # 1 dakikalık mumlar
                limit = max(window_minutes * 3, 30)  # yeterli kapsam
                params = {"step": step, "limit": limit}
                async with session.get(ohlcv_url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status != 200:
                        logger.debug(f"Bitstamp OHLCV failed: status={resp.status}")
                        return None
                    ohlcv_data = await resp.json()
                    candles = ohlcv_data.get("data", {}).get("ohlc", [])
                    if not candles:
                        return None

                    # Market window start/end timestamp (UTC)
                    today = now.date()
                    from datetime import timedelta
                    start_ts = datetime(today.year, today.month, today.day,
                                        start_utc_h, start_m, tzinfo=timezone.utc).timestamp()
                    end_ts = datetime(today.year, today.month, today.day,
                                      end_utc_h, end_m, tzinfo=timezone.utc).timestamp()

                    # Window içindeki mumları filtrele
                    window_candles = []
                    for c in candles:
                        ts = float(c.get("timestamp", 0))
                        if start_ts <= ts < end_ts:
                            window_candles.append(c)

                    if window_candles:
                        window_open = float(window_candles[0]["open"])
                        window_close = float(window_candles[-1]["close"])
                    elif len(candles) >= 2:
                        # Fallback: son mumları kullan (timestamp filter çalışmadıysa)
                        n_candles = max(1, window_minutes)
                        relevant = candles[-n_candles:] if len(candles) >= n_candles else candles
                        window_open = float(relevant[0]["open"])
                        window_close = float(relevant[-1]["close"])
                    else:
                        return None

                    if window_close > window_open:
                        logger.info(
                            f"SPOT_RESOLVE: {question[:50]} → UP "
                            f"(open={window_open:.2f}, close={window_close:.2f}, "
                            f"Δ={((window_close/window_open)-1)*100:+.3f}%)"
                        )
                        return "YES"
                    else:
                        logger.info(
                            f"SPOT_RESOLVE: {question[:50]} → DOWN/FLAT "
                            f"(open={window_open:.2f}, close={window_close:.2f}, "
                            f"Δ={((window_close/window_open)-1)*100:+.3f}%)"
                        )
                        return "NO"
        except Exception as e:
            logger.debug(f"Spot resolution hatası: {e}")
            return None

    def _close_position_neutral(self, market_id: str):
        """Dolmamış emir — USDC iade edildi, PnL = 0."""
        pos = self.data["positions"].get(market_id)
        if pos is None:
            return

        # Duplicate guard: aynı order_id zaten closed'daysa pozisyonu sil ama tekrar kaydetme
        existing_ids = {c.get("order_id") for c in self.data["closed"]}
        if pos.get("order_id") in existing_ids:
            self.data["positions"].pop(market_id, None)
            return

        self.data["positions"].pop(market_id)
        pos["close_price"] = pos["entry_price"]
        pos["pnl"] = 0.0
        pos["payout"] = pos["amount"]
        pos["result"] = "NEUTRAL"

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
        pos = self.data["positions"].get(market_id)
        if pos is None:
            return

        # Duplicate guard: aynı order_id zaten closed'daysa pozisyonu sil ama capital'e dokunma
        existing_ids = {c.get("order_id") for c in self.data["closed"]}
        if pos.get("order_id") in existing_ids:
            self.data["positions"].pop(market_id, None)
            logger.debug(f"Duplicate closed trade atlandı: {pos.get('order_id')}")
            return

        self.data["positions"].pop(market_id)
        amount = pos["amount"]
        entry = pos["entry_price"]
        shares = amount / entry if entry > 0 else 0
        payout = shares * token_close_price
        pnl = payout - amount

        pos["close_price"] = round(token_close_price, 4)
        pos["pnl"] = round(pnl, 2)
        pos["payout"] = round(payout, 2)
        pos["result"] = "WIN" if pnl > 0 else ("NEUTRAL" if pnl == 0 else "LOSS")

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
        pnl_pct = (total_pnl / self.initial_capital * 100) if self.initial_capital > 0 else 0.0
        target_pct = (capital / target * 100) if target > 0 else 0.0
        logger.info(f"Toplam PnL : ${total_pnl:.2f} ({pnl_pct:.1f}%)")
        logger.info(f"Hedef      : ${target:.0f} ({target_pct:.1f}% tamamlandı)")
        logger.info(f"Açık Poz   : {open_pos}")
        logger.info("=" * 50)
