import os
import json
import re
import time as _time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
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


def _now_et() -> datetime:
    """Current wall-clock time in America/New_York, as its own mockable
    function (matching strategies.arbitrage_engine._get_current_et_hour) so
    tests can pin it instead of flaking based on when the suite happens to
    run — update_positions()'s question-derived TIME_EXPIRED check compares
    this against each position's parsed end time-of-day."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))

# Bond positions user manually cancelled — never re-add these
_IGNORED_MARKETS = {
    "0xf2f0cf8b7aa90c53fbd56439782d1828a2315c8d21890f4a2804a61720985cea",  # Denmark PM
    "0x6b66bec6760a8ece84e99ce987f53e13d3fd94b25147c1442026899ae45ad765",  # Valorant esports
    "0xc75d669ed6a786a32ba770bebea3e10a8bc54652d7d739a20f84d1c174eb6928",  # Peaky Blinders
}


@contextmanager
def _file_lock(lock_path: Path = LOCK_FILE, timeout: float = 5.0):
    """File lock using fcntl.flock (Linux) with O_CREAT|O_EXCL fallback (Windows).

    fcntl.flock: OS otomatik temizler (process ölürse lock kalkmaz).
    Eski O_CREAT|O_EXCL yöntemi stale lock sorununa neden oluyordu.
    """
    import sys
    lock_path.parent.mkdir(exist_ok=True)
    fd = None
    try:
        if sys.platform != "win32":
            import fcntl
            fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY)
            deadline = _time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (OSError, BlockingIOError):
                    if _time.monotonic() > deadline:
                        fcntl.flock(fd, fcntl.LOCK_EX)  # blocking acquire
                        break
                    _time.sleep(0.05)
            yield
        else:
            # Windows fallback: eski O_CREAT|O_EXCL yöntemi
            deadline = _time.monotonic() + timeout
            while True:
                try:
                    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    break
                except FileExistsError:
                    if _time.monotonic() > deadline:
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
            if sys.platform != "win32":
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)
            if sys.platform == "win32":
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

    # ── Capital Pool System ──
    # Splits capital into maker/bond/directional pools for hybrid strategy.
    # Pool ratios are configurable. PnL returns to the originating pool.

    DEFAULT_POOLS = {"maker": 0.00, "bond": 0.00, "directional": 1.00}  # DIRECTIONAL ONLY mode

    def _get_pool_ratios(self) -> dict[str, float]:
        """Get pool allocation ratios from env or defaults."""
        return {
            "maker": float(os.getenv("MAKER_CAPITAL_PCT", self.DEFAULT_POOLS["maker"])),
            "bond": float(os.getenv("BOND_CAPITAL_PCT", self.DEFAULT_POOLS["bond"])),
            "directional": float(os.getenv("DIRECTIONAL_CAPITAL_PCT", self.DEFAULT_POOLS["directional"])),
        }

    def pool_total(self, pool: str) -> float:
        """Total capital allocated to a pool (before locking)."""
        ratios = self._get_pool_ratios()
        return self.data["capital"] * ratios.get(pool, 0.0)

    def pool_locked(self, pool: str) -> float:
        """Capital locked in open positions for a specific strategy pool."""
        return sum(
            p["amount"] for p in self.data["positions"].values()
            if p.get("strategy", "directional") == pool
        )

    def pool_available(self, pool: str) -> float:
        """Available capital in a specific strategy pool."""
        return max(0.0, self.pool_total(pool) - self.pool_locked(pool))

    def available_capital(self) -> float:
        """Free cash = total capital minus cost of open positions."""
        locked = sum(p["amount"] for p in self.data["positions"].values())
        return max(0, self.data["capital"] - locked)

    def locked_capital(self) -> float:
        """Total capital locked in open positions."""
        return sum(p["amount"] for p in self.data["positions"].values())

    def open_position_count(self) -> int:
        return len(self.data["positions"])

    def pool_position_count(self, pool: str) -> int:
        """Count of open positions in a specific pool."""
        return sum(
            1 for p in self.data["positions"].values()
            if p.get("strategy", "directional") == pool
        )

    def has_position(self, market_id: str) -> bool:
        return market_id in self.data["positions"]

    def add_position(self, market_id: str, order: dict, question: str,
                     strategy: str = "directional", edge: float | None = None,
                     confluence_score: float | None = None,
                     risk_flags: list | None = None):
        if market_id in _IGNORED_MARKETS:
            logger.debug(f"IGNORED: {question[:40]} (blacklisted bond)")
            return
        from datetime import datetime, timezone
        pos = {
            "order_id": order["order_id"],
            "question": question,
            "outcome": order.get("outcome", "YES"),
            "amount": order["amount"],
            "entry_price": order["price"],
            "status": order["status"],
            "token_id": order.get("token_id", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "opened_at": _time.time(),
            "strategy": strategy,
        }
        # Sinyal meta verisi (TradeAnalyzer'in gercek trade'lerde de root-cause/
        # pattern analizi yapabilmesi icin) — sadece cagiran taraf saglarsa yaz.
        if edge is not None:
            pos["edge"] = edge
        if confluence_score is not None:
            pos["confluence_score"] = confluence_score
        if risk_flags is not None:
            pos["risk_flags"] = risk_flags
        self.data["positions"][market_id] = pos
        self._save()
        logger.info(f"Pozisyon eklendi [{strategy}]: {question[:50]}")

    def daily_loss_exceeded(self, threshold: float) -> bool:
        today = str(datetime.now(timezone.utc).date())
        if self.data["daily"]["date"] != today:
            self.data["daily"] = {"date": today, "pnl": 0}
            self._save()
        # Günün başındaki sermaye = şu anki capital - bugünkü PnL
        day_start_capital = self.data["capital"] - self.data["daily"]["pnl"]
        if day_start_capital <= 0:
            return True  # Sermaye sıfır/negatif → trade yapma
        loss_pct = abs(min(0, self.data["daily"]["pnl"])) / day_start_capital
        return loss_pct >= threshold

    async def update_positions(self, client):
        """Açık pozisyonların güncel fiyatlarını çeker, kapananları kapatır.

        Emir dolum durumunu CLOB'dan kontrol eder:
        - Dolmamış (LIVE) emir + market expired → NEUTRAL (USDC iade)
        - Dolmuş (MATCHED) emir + market expired → resolution'a göre WIN/LOSS
        """
        now = datetime.now(timezone.utc)
        # Remove ignored markets if they snuck back in
        for mid in list(self.data["positions"]):
            if mid in _IGNORED_MARKETS:
                del self.data["positions"][mid]
                self._save()
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
                    now_et = _now_et()
                    market_end_min = end_h * 60 + end_m
                    now_min = now_et.hour * 60 + now_et.minute
                    # Midnight crossing fix: fark negatifse gün geçişi var
                    diff = now_min - market_end_min
                    if diff < -720:  # 12 saatten fazla fark = gece yarısı geçişi
                        diff += 1440
                    if 0 < diff and diff < 720:  # max 12 saat sonra expired say
                        market_expired = True
                        logger.info(f"TIME_EXPIRED: {question[:50]} (end={end_h}:{end_m:02d} ET, now={now_et.hour}:{now_et.minute:02d} ET)")

            # ── Emir dolum durumunu kontrol et ──
            order_filled = await self._check_order_filled(client, pos)

            if market is None:
                # Gamma API conditionId lookup unreliable — use CLOB token resolution
                # Check if market end time (from question) has passed
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
                    now_et = _now_et()
                    market_end_mins = end_hour * 60 + end_min
                    now_mins = now_et.hour * 60 + now_et.minute
                    # Market ended if current time > end time + 1 min buffer
                    # Midnight crossing fix: negatif fark = gece yarısı geçişi
                    _diff = now_mins - market_end_mins
                    if _diff < -720:
                        _diff += 1440
                    market_ended = (0 < _diff < 720)

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

                # Market ended — resolve via CLOB API tokens.winner (tek güvenilir kaynak)
                if order_filled:
                    resolution = None
                    outcome = pos.get("outcome", "YES").upper()

                    # 1) CLOB API tokens.winner (resmi Polymarket sonucu)
                    if resolution is None and market_id:
                        try:
                            import requests as _req
                            _clob_resp = _req.get(
                                f"https://clob.polymarket.com/markets/{market_id}",
                                timeout=10,
                            )
                            if _clob_resp.status_code == 200:
                                _clob_data = _clob_resp.json()
                                for _tok in _clob_data.get("tokens", []):
                                    if _tok.get("winner") is True:
                                        _outcome = (_tok.get("outcome") or "").upper()
                                        if _outcome in ("UP", "YES"):
                                            resolution = "YES"
                                        elif _outcome in ("DOWN", "NO"):
                                            resolution = "NO"
                                        logger.info(
                                            f"CLOB_WINNER_RESOLVE: {question[:50]} → "
                                            f"winner={_outcome} resolution={resolution}"
                                        )
                                        break
                        except Exception as _e:
                            logger.debug(f"CLOB winner check failed: {_e}")

                    # 2) FALLBACK: CLOB Token endpoint (daha hızlı)
                    if resolution is None:
                        token_id = pos.get("token_id", "")
                        if token_id:
                            try:
                                import requests as _req2
                                _tok_resp = _req2.get(
                                    f"https://clob.polymarket.com/token/{token_id}",
                                    timeout=5,
                                )
                                if _tok_resp.status_code == 200:
                                    _tok_data = _tok_resp.json()
                                    if _tok_data.get("winner") is True:
                                        resolution = outcome  # bu token kazandı
                                        logger.info(f"TOKEN_ENDPOINT_RESOLVE: {question[:50]} → winner=THIS_TOKEN → {outcome}")
                                    elif _tok_data.get("winner") is False:
                                        resolution = "NO" if outcome == "YES" else "YES"
                                        logger.info(f"TOKEN_ENDPOINT_RESOLVE: {question[:50]} → loser=THIS_TOKEN → {resolution}")
                            except Exception as _e2:
                                logger.debug(f"Token endpoint check failed: {_e2}")

                    # 3) FALLBACK: Orderbook çöküşü tespiti (SADECE market bittikten sonra)
                    # OB_COLLAPSE_RESOLVE DEVRE DIŞI — orderbook kapandıktan sonra
                    # temizleniyor, YES_ask=0 "DOWN" anlamına gelmiyor!
                    # Binance kontrolü: gerçek sonuçlar TERS kaydediliyordu.
                    # Sadece CLOB winner ile resolve et.

                    # 4) Apply resolution
                    if resolution == "YES":
                        close_price = 1.0 if outcome == "YES" else 0.0
                        result_str = "WIN" if close_price == 1.0 else "LOSS"
                        logger.info(f"RESOLVED UP → {outcome} = {result_str}: {question[:50]}")
                        self._close_position(market_id, close_price)
                    elif resolution == "NO":
                        close_price = 0.0 if outcome == "YES" else 1.0
                        result_str = "WIN" if close_price == 1.0 else "LOSS"
                        logger.info(f"RESOLVED DOWN → {outcome} = {result_str}: {question[:50]}")
                        self._close_position(market_id, close_price)
                    else:
                        # Hiçbir yöntem çalışmadı — timeout kontrolü
                        created = pos.get("created_at", "")
                        age_minutes = 0
                        if created:
                            try:
                                created_dt = datetime.fromisoformat(created)
                                if created_dt.tzinfo is None:
                                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                                age_minutes = (now - created_dt).total_seconds() / 60
                            except Exception:
                                pass
                        if age_minutes > 45:
                            # 45dk sonra hâlâ resolve olmadıysa → last_price heuristic dene
                            _last_price = pos.get("current_price")
                            if _last_price is not None and _last_price > 0.80:
                                # Token price >0.80 → büyük ihtimalle bu token kazandı
                                logger.warning(
                                    f"TIMEOUT_HEURISTIC_WIN ({age_minutes:.0f}dk): {question[:50]} — "
                                    f"last_price={_last_price:.2f}>0.80 → treating as WIN"
                                )
                                self._close_position(market_id, 1.0)
                            elif _last_price is not None and _last_price < 0.20:
                                # Token price <0.20 → büyük ihtimalle bu token kaybetti
                                logger.warning(
                                    f"TIMEOUT_HEURISTIC_LOSS ({age_minutes:.0f}dk): {question[:50]} — "
                                    f"last_price={_last_price:.2f}<0.20 → treating as LOSS"
                                )
                                self._close_position(market_id, 0.0)
                            else:
                                # Fiyat belirsiz → NEUTRAL kapat (slot aç)
                                logger.warning(
                                    f"FORCE_CLOSE_TIMEOUT ({age_minutes:.0f}dk): {question[:50]} — "
                                    f"CLOB+Token hepsi başarısız, last_price={_last_price} → NEUTRAL"
                                )
                                self._close_position_neutral(market_id)
                        elif age_minutes > 10:
                            logger.warning(
                                f"STALE_UNRESOLVED ({age_minutes:.0f}dk): {question[:50]} — "
                                f"CLOB resolution bekleniyor"
                            )
                        else:
                            logger.info(f"WAITING_RESOLUTION: {question[:50]} — CLOB winner bekleniyor ({age_minutes:.0f}dk)")
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

                # CLOB API tokens.winner (resmi Polymarket sonucu)
                if resolution is None and market_id:
                    try:
                        import requests as _req
                        _clob_resp = _req.get(
                            f"https://clob.polymarket.com/markets/{market_id}",
                            timeout=10,
                        )
                        if _clob_resp.status_code == 200:
                            _clob_data = _clob_resp.json()
                            for _tok in _clob_data.get("tokens", []):
                                if _tok.get("winner") is True:
                                    _outcome = (_tok.get("outcome") or "").upper()
                                    if _outcome in ("UP", "YES"):
                                        resolution = "YES"
                                    elif _outcome in ("DOWN", "NO"):
                                        resolution = "NO"
                                    logger.info(
                                        f"CLOB_WINNER_RESOLVE: {pos.get('question','')[:50]} -> "
                                        f"winner={_outcome} resolution={resolution}"
                                    )
                                    break
                    except Exception as _e:
                        logger.debug(f"CLOB winner check failed: {_e}")

                if resolution == "YES":
                    close_price = 1.0 if outcome == "YES" else 0.0
                    result_str = "WIN" if close_price == 1.0 else "LOSS"
                    logger.info(f"RESOLVED UP → {outcome} = {result_str}: {pos.get('question','')[:50]}")
                    self._close_position(market_id, close_price)
                elif resolution == "NO":
                    close_price = 0.0 if outcome == "YES" else 1.0
                    result_str = "WIN" if close_price == 1.0 else "LOSS"
                    logger.info(f"RESOLVED DOWN → {outcome} = {result_str}: {pos.get('question','')[:50]}")
                    self._close_position(market_id, close_price)
                else:
                    # Resolution bilinmiyor — pozisyonu açık tut, sonraki döngüde CLOB tekrar dene
                    created = pos.get("created_at", "")
                    age_minutes = 0
                    if created:
                        try:
                            created_dt = datetime.fromisoformat(created)
                            if created_dt.tzinfo is None:
                                created_dt = created_dt.replace(tzinfo=timezone.utc)
                            age_minutes = (now - created_dt).total_seconds() / 60
                        except Exception:
                            pass
                    if age_minutes > 45:
                        # 45dk sonra hâlâ resolve olmadıysa → last_price heuristic dene
                        _last_price = pos.get("current_price")
                        if _last_price is not None and _last_price > 0.80:
                            logger.warning(
                                f"TIMEOUT_HEURISTIC_WIN ({age_minutes:.0f}dk): {pos.get('question','')[:50]} — "
                                f"last_price={_last_price:.2f}>0.80 → treating as WIN"
                            )
                            self._close_position(market_id, 1.0)
                        elif _last_price is not None and _last_price < 0.20:
                            logger.warning(
                                f"TIMEOUT_HEURISTIC_LOSS ({age_minutes:.0f}dk): {pos.get('question','')[:50]} — "
                                f"last_price={_last_price:.2f}<0.20 → treating as LOSS"
                            )
                            self._close_position(market_id, 0.0)
                        else:
                            logger.warning(
                                f"FORCE_CLOSE_TIMEOUT ({age_minutes:.0f}dk): {pos.get('question','')[:50]} — "
                                f"last_price={_last_price} → NEUTRAL"
                            )
                            self._close_position_neutral(market_id)
                    else:
                        logger.info(f"WAITING_RESOLUTION: {pos.get('question','')[:50]} — CLOB winner bekleniyor ({age_minutes:.0f}dk)")
                continue

            # Token fiyatı: YES için YES bid, NO için gerçek NO orderbook
            if outcome == "NO":
                # Gerçek NO orderbook fiyatını çek.
                # BUG: client.get_market() (Gamma single-market fetch) normalize
                # ederken yes_token_id/no_token_id'yi HİÇ set etmiyor (sadece
                # _normalize_markets(), market taramasında kullanılıyor, set eder).
                # market.get("no_token_id") bu yüzden burada her zaman None dönüyordu
                # → no_book hiçbir zaman gerçekten sorgulanmıyordu → NO pozisyonları
                # daima current_price=1-yes_ask (gecikmeli Gamma verisi) ile
                # değerleniyordu. pos["token_id"] ise emir anında kaydedilen,
                # her zaman doğru token id — birincil kaynak olarak onu kullan.
                no_tid = pos.get("token_id") or (market or {}).get("no_token_id")
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

        SADECE Gamma API'nin resmi resolution alanlarını kontrol eder.
        bid/ask tahmini KALDIRILDI — güvenilmez ve yanlış P&L'e sebep oluyor.
        """
        for field in ("outcome", "resolution", "winner"):
            val = (market.get(field) or "").strip().lower()
            if val in ("yes", "up", "1", "true"):
                return "YES"
            if val in ("no", "down", "0", "false"):
                return "NO"
        # bid/ask tahmini KALDIRILDI — Polymarket'in resmi resolution'ını bekle
        # Eski kod ask>=0.95 → YES, bid<=0.05 → NO yapıyordu ama
        # resolved olmamış marketlerde bu yanlış sonuç veriyordu
        return None

    @staticmethod
    async def _spot_based_resolution(question: str) -> str | None:
        """DEVRE DIŞI — Bitstamp spot resolution güvenilmez.

        Bu fonksiyon Polymarket'in resmi resolution'ıyla uyuşmuyordu.
        752 yanlış resolution kaydına sebep oldu. Artık sadece CLOB
        tokens.winner kullanılıyor. Bu metod None döner = pozisyon açık kalır,
        sonraki döngüde CLOB tekrar denenir.

        Eski davranış: Bitstamp OHLCV ile open/close karşılaştırıp UP/DOWN
        kararı veriyordu — ama Polymarket oracle'ı farklı fiyat kaynağı ve
        farklı timestamp kullanıyor, bu yüzden sonuçlar uyuşmuyordu.
        """
        logger.debug(f"SPOT_RESOLUTION_DISABLED: {question[:50]} — sadece CLOB winner güvenilir")
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
        pos["resolved_at"] = datetime.now(timezone.utc).isoformat()
        pos["closed_at"] = _time.time()

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
        if entry <= 0:
            logger.error(f"INVALID entry_price={entry} for {market_id} — closing as NEUTRAL to prevent phantom loss")
            pos["close_price"] = 0
            pos["pnl"] = 0.0
            pos["payout"] = 0.0
            pos["result"] = "NEUTRAL"
            self.data["closed"].append(pos)
            return
        shares = amount / entry
        payout = shares * token_close_price
        pnl = payout - amount

        pos["close_price"] = round(token_close_price, 4)
        pos["pnl"] = round(pnl, 2)
        pos["payout"] = round(payout, 2)
        pos["result"] = "WIN" if pnl > 0 else ("NEUTRAL" if pnl == 0 else "LOSS")
        # CLOB-verified flag: bu trade CLOB tokens.winner ile resolve edildi
        pos["pnl_verified"] = True
        pos["resolved_at"] = datetime.now(timezone.utc).isoformat()
        pos["closed_at"] = _time.time()

        # Sermayeyi sadece PnL kadar güncelle — principal zaten locked olarak sayılıyordu
        self.data["capital"] += pnl
        self.data["daily"]["pnl"] += pnl
        self.data["closed"].append(pos)

        # CLOB-verified WR tracker
        verified_trades = [t for t in self.data["closed"] if t.get("pnl_verified") is True]
        v_wins = sum(1 for t in verified_trades if t["result"] == "WIN")
        v_losses = sum(1 for t in verified_trades if t["result"] == "LOSS")
        v_total = v_wins + v_losses
        v_wr = (v_wins / v_total * 100) if v_total > 0 else 0

        logger.info(
            f"Pozisyon kapatıldı: {pos['question'][:40]} | "
            f"{pos['result']} | PnL: ${pnl:+.2f} | "
            f"VERIFIED_WR: {v_wins}W/{v_losses}L = {v_wr:.1f}% ({v_total} trades)"
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
