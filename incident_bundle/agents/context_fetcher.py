"""
Context Fetcher — Market tipine göre harici veri çeker.

WEATHER  → Open-Meteo API (tamamen ücretsiz, kayıt gerektirmez)
SPORTS   → ESPN public API (ücretsiz, key gerektirmez)
Diğerleri → boş context (AI kendi yorumunu yapar)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from agents.market_classifier import classify

OPEN_METEO_GEO = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports"

# Şehir adı → (lat, lon) cache (sık geçen şehirler)
_CITY_CACHE: dict[str, tuple[float, float]] = {
    "new york": (40.7128, -74.0060),
    "los angeles": (34.0522, -118.2437),
    "chicago": (41.8781, -87.6298),
    "toronto": (43.6532, -79.3832),
    "london": (51.5074, -0.1278),
    "paris": (48.8566, 2.3522),
    "miami": (25.7617, -80.1918),
    "houston": (29.7604, -95.3698),
    "dallas": (32.7767, -96.7970),
    "boston": (42.3601, -71.0589),
    "denver": (39.7392, -104.9903),
    "phoenix": (33.4484, -112.0740),
    "seattle": (47.6062, -122.3321),
    "atlanta": (33.7490, -84.3880),
    "minneapolis": (44.9778, -93.2650),
    "washington": (38.9072, -77.0369),
    "san francisco": (37.7749, -122.4194),
    "istanbul": (41.0082, 28.9784),
    "ankara": (39.9334, 32.8597),
}

# ESPN league slug eşleştirmesi
_ESPN_LEAGUES = {
    "NBA": ("basketball", "nba"),
    "NFL": ("football", "nfl"),
    "FOOTBALL": ("soccer", "fifa.world"),
    "TENNIS": ("tennis", "atp"),
    "MOTORSPORTS": ("racing", "f1"),
    "NHL": ("hockey", "nhl"),
    "MLB": ("baseball", "mlb"),
}


async def fetch_context(market: dict) -> dict:
    """
    market dict'inden soruyu okuyup kategori tespiti yapar,
    ilgili harici veriyi çekip döner.

    Dönüş formatı:
    {
        "category": "WEATHER",
        "subcategory": "TEMPERATURE",
        "external_data": {...}   # API'den gelen ham/özet veri
        "summary": "..."         # Claude'a verilecek kısa metin
    }
    """
    question = market.get("question", "")
    classification = classify(question)
    category = classification["category"]

    ctx: dict[str, Any] = {
        "category": category,
        "subcategory": classification["subcategory"],
        "tags": classification["tags"],
        "external_data": {},
        "summary": "",
    }

    try:
        if category == "WEATHER":
            ctx = await _fetch_weather_context(question, ctx)
        elif category == "SPORTS":
            ctx = await _fetch_sports_context(classification, ctx)
        elif category in ("CRYPTO", "FINANCE"):
            ctx = await _fetch_crypto_context(question, ctx)
    except Exception as e:
        logger.warning(f"Context fetch hatası ({category}): {e}")

    return ctx


# ──────────────────────────────────────────────────────────────────────────────
# WEATHER
# ──────────────────────────────────────────────────────────────────────────────

async def _fetch_weather_context(question: str, ctx: dict) -> dict:
    """Open-Meteo ile gerçek hava durumu tahmini çek."""
    city = _extract_city(question)
    if not city:
        ctx["summary"] = "Hava durumu marketi ama şehir tespit edilemedi."
        return ctx

    lat, lon = await _get_coordinates(city)
    if lat is None:
        ctx["summary"] = f"'{city}' koordinatı bulunamadı."
        return ctx

    # Günlük max/min sıcaklık + yağış tahmini (7 gün)
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
        "forecast_days": 7,
        "timezone": "auto",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(OPEN_METEO_FORECAST, params=params)
        r.raise_for_status()
        data = r.json()

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])

    forecast_lines = []
    for i, date in enumerate(dates[:7]):
        t_max = max_temps[i] if i < len(max_temps) else "?"
        t_min = min_temps[i] if i < len(min_temps) else "?"
        rain = precip[i] if i < len(precip) else 0
        forecast_lines.append(f"{date}: max={t_max}°C min={t_min}°C yağış={rain}mm")

    ctx["external_data"] = {
        "city": city,
        "lat": lat,
        "lon": lon,
        "forecast": dict(zip(dates, zip(max_temps, min_temps, precip))),
    }
    ctx["summary"] = (
        f"Hava durumu tahmini — {city.title()} (Open-Meteo):\n"
        + "\n".join(forecast_lines)
    )
    logger.debug(f"Weather context alındı: {city}")
    return ctx


def _extract_city(question: str) -> str | None:
    """Soru metninden şehir adını çıkar."""
    # Önce cache'deki şehirleri ara
    q_lower = question.lower()
    for city in _CITY_CACHE:
        if city in q_lower:
            return city

    # "in <City>" veya "at <City>" kalıpları
    m = re.search(r"\b(?:in|at|for)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", question)
    if m:
        return m.group(1).lower()

    return None


async def _get_coordinates(city: str) -> tuple[float | None, float | None]:
    """Şehir → koordinat. Önce cache, sonra Open-Meteo geocoding."""
    if city in _CITY_CACHE:
        return _CITY_CACHE[city]

    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.get(OPEN_METEO_GEO, params={"name": city, "count": 1})
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None, None
        loc = results[0]
        coords = (loc["latitude"], loc["longitude"])
        _CITY_CACHE[city] = coords  # cache'e ekle
        return coords


# ──────────────────────────────────────────────────────────────────────────────
# SPORTS
# ──────────────────────────────────────────────────────────────────────────────

async def _fetch_sports_context(classification: dict, ctx: dict) -> dict:
    """ESPN public API'den güncel skor/maç verisi çek."""
    subcategory = classification.get("subcategory", "")
    league_info = _ESPN_LEAGUES.get(subcategory)

    if not league_info:
        ctx["summary"] = f"Spor marketi ({subcategory}) — harici veri kaynağı yok."
        return ctx

    sport, league = league_info
    url = f"{ESPN_SCOREBOARD}/{sport}/{league}/scoreboard"

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        if r.status_code != 200:
            ctx["summary"] = f"ESPN API erişilemiyor (HTTP {r.status_code})."
            return ctx
        data = r.json()

    events = data.get("events", [])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    game_lines = []
    for event in events[:10]:  # Max 10 maç
        name = event.get("name", "")
        status = event.get("status", {}).get("type", {}).get("description", "")
        date = event.get("date", "")[:10]

        competitions = event.get("competitions", [{}])
        if competitions:
            comp = competitions[0]
            competitors = comp.get("competitors", [])
            scores = []
            for c in competitors:
                team = c.get("team", {}).get("displayName", "?")
                score = c.get("score", "?")
                record = c.get("records", [{}])[0].get("summary", "") if c.get("records") else ""
                scores.append(f"{team} {score} ({record})")
            game_lines.append(f"{date} | {' vs '.join(scores)} | {status}")

    ctx["external_data"] = {
        "league": f"{sport}/{league}",
        "event_count": len(events),
        "events": game_lines,
    }
    ctx["summary"] = (
        f"ESPN {subcategory} verileri ({today}):\n"
        + ("\n".join(game_lines) if game_lines else "Aktif maç bulunamadı.")
    )
    logger.debug(f"Sports context alındı: {subcategory}, {len(game_lines)} maç")
    return ctx


# ──────────────────────────────────────────────────────────────────────────────
# CRYPTO / FINANCE
# ──────────────────────────────────────────────────────────────────────────────

_COIN_MAP = {
    "bitcoin": ("bitcoin", "BTC"),
    "btc":     ("bitcoin", "BTC"),
    "ethereum":("ethereum", "ETH"),
    "eth":     ("ethereum", "ETH"),
    "solana":  ("solana", "SOL"),
    "sol":     ("solana", "SOL"),
    "xrp":     ("ripple", "XRP"),
    "ripple":  ("ripple", "XRP"),
    "bnb":     ("binancecoin", "BNB"),
    "dogecoin":("dogecoin", "DOGE"),
    "doge":    ("dogecoin", "DOGE"),
    "hype":    ("hyperliquid", "HYPE"),
}

# ── Cycle-level cache: tüm döngüde bir kez çek, tüm marketlerde kullan ──────
_PRICE_CACHE: dict = {}   # coin_id → price data
_CACHE_TS: float = 0.0    # son güncelleme zamanı (time.monotonic)
_CACHE_TTL: float = 55.0  # saniye


async def _get_cached_prices(coin_ids: list[str]) -> dict:
    """CoinGecko fiyatlarını cycle TTL ile cache'le — 55s'de bir yenile."""
    import time
    global _PRICE_CACHE, _CACHE_TS
    now = time.monotonic()
    missing = [c for c in coin_ids if c not in _PRICE_CACHE]
    if missing or (now - _CACHE_TS) > _CACHE_TTL:
        all_ids = list(set(list(_PRICE_CACHE.keys()) + missing))
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={
                        "ids": ",".join(all_ids),
                        "vs_currencies": "usd",
                        "include_24hr_change": "true",
                        "include_24hr_vol": "true",
                        "include_7d_change": "true",
                    },
                )
            if r.status_code == 200:
                _PRICE_CACHE.update(r.json())
                _CACHE_TS = now
        except Exception as e:
            logger.debug(f"CoinGecko cache hatası: {e}")
    return _PRICE_CACHE


async def _fetch_crypto_context(question: str, ctx: dict) -> dict:
    """Anlık kripto fiyatı ve trend verisi — CRYPTO/FINANCE marketler için.
    Cache kullanır: aynı döngüde tek API çağrısı yapılır."""
    q_lower = question.lower()

    # Hangi coin soruluyor?
    coin_id, coin_sym = "bitcoin", "BTC"
    for key, (cid, sym) in _COIN_MAP.items():
        if key in q_lower:
            coin_id, coin_sym = cid, sym
            break

    # "Up or Down" marketi mi? (5dk/15dk/1sa kısa vadeli)
    is_updown = "up or down" in q_lower

    # Fiyat hedefi var mı? "reach $74,000" / "above $72,000"
    target = None
    m = re.search(r'\$\s*([\d,]+(?:\.\d+)?)', question)
    if m:
        try:
            target = float(m.group(1).replace(",", ""))
        except Exception:
            pass

    prices = await _get_cached_prices([coin_id])
    data = prices.get(coin_id, {})
    price      = data.get("usd", 0) or 0
    change_24h = data.get("usd_24h_change", 0) or 0
    change_7d  = data.get("usd_7d_change", 0) or 0
    vol_24h    = data.get("usd_24h_vol", 0) or 0

    lines = []
    if price:
        lines.append(f"{coin_sym} ANLIK FİYAT : ${price:,.2f}")
        lines.append(f"24 saatlik değişim : {change_24h:+.2f}%")
        lines.append(f"7 günlük değişim   : {change_7d:+.2f}%")
        lines.append(f"24s hacim          : ${vol_24h:,.0f}")

        if target and price > 0:
            pct = (target - price) / price * 100
            dir_str = "YUKARI" if pct > 0 else "AŞAĞI"
            lines.append(f"Hedef fiyat : ${target:,.0f}  →  hedefe {abs(pct):.1f}% {dir_str}")

        # Momentum
        if change_24h > 4:    mom = "GÜÇLÜ YÜKSELİŞ ↑↑"
        elif change_24h > 1.5: mom = "HAFIF YÜKSELİŞ ↑"
        elif change_24h < -4:  mom = "GÜÇLÜ DÜŞÜŞ ↓↓"
        elif change_24h < -1.5:mom = "HAFIF DÜŞÜŞ ↓"
        else:                  mom = "YATAY / NÖTR →"
        lines.append(f"Momentum    : {mom}")

        if is_updown:
            lines.append("")
            lines.append("⚠ BU 'UP OR DOWN' MARKETİ — YES = FİYAT YÜKSELİR")
            lines.append("Kısa vadeli (5-60 dakika) tahmin yap.")
            lines.append(f"Mevcut momentum '{mom}' baz alınarak, kapanışa kadar yukarı gitme olasılığı nedir?")
    else:
        lines.append(f"{coin_sym} fiyat verisi alınamadı.")

    ctx["summary"] = "\n".join(lines)
    logger.debug(f"Crypto context: {coin_sym} ${price:,.0f} {change_24h:+.1f}%")
    return ctx
