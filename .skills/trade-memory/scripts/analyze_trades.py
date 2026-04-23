#!/usr/bin/env python3
"""
Trade Memory Analyzer — Polymarket bot trade geçmişini analiz eder.

Okur: data/positions.json (kapalı trade'ler)
Yazar: data/trade_memory.json (canlı hafıza)

Her çalıştırıldığında tüm geçmişi yeniden analiz eder ve kuralları günceller.
"""

import json
import re
import os
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Proje kök dizini (script'ten 3 seviye yukarı)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
POSITIONS_FILE = DATA_DIR / "positions.json"
MEMORY_FILE = DATA_DIR / "trade_memory.json"


def load_trades() -> list[dict]:
    """positions.json'dan kapalı trade'leri yükle."""
    if not POSITIONS_FILE.exists():
        print(f"HATA: {POSITIONS_FILE} bulunamadı.")
        sys.exit(1)
    with open(POSITIONS_FILE) as f:
        data = json.load(f)
    return data.get("closed", []), data.get("capital", 0), data.get("positions", {})


def detect_coin(question: str) -> str:
    """Market sorusundan coin adını çıkar."""
    q = question.lower()
    for name, label in [
        ("bitcoin", "BTC"), ("btc", "BTC"),
        ("ethereum", "ETH"), ("eth", "ETH"),
        ("solana", "SOL"), ("sol", "SOL"),
        ("xrp", "XRP"),
        ("dogecoin", "DOGE"), ("doge", "DOGE"),
        ("bnb", "BNB"),
        ("hyperliquid", "HYPE"), ("hype", "HYPE"),
    ]:
        if name in q:
            return label
    return "UNKNOWN"


def detect_timeframe(question: str) -> str:
    """Market sorusundan zaman dilimini çıkar."""
    m = re.search(
        r'(\d{1,2}):(\d{2})(?:AM|PM)\s*[-–]\s*(\d{1,2}):(\d{2})(?:AM|PM)',
        question, re.IGNORECASE,
    )
    if m:
        h1, m1, h2, m2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        mins = (h2 * 60 + m2) - (h1 * 60 + m1)
        if mins < 0:
            mins += 1440
        if mins <= 7:
            return "5m"
        elif mins <= 20:
            return "15m"
        elif mins <= 90:
            return "1h"
        else:
            return "4h"
    return "unknown"


def detect_date(question: str) -> str | None:
    """Market sorusundan tarihi çıkar (March 21 → 2026-03-21)."""
    m = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})', question, re.IGNORECASE)
    if m:
        month_names = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
        }
        month = month_names.get(m.group(1).lower(), 0)
        day = int(m.group(2))
        if month > 0:
            return f"2026-{month:02d}-{day:02d}"
    return None


def analyze_overall(trades: list[dict]) -> dict:
    """Genel performans metrikleri."""
    if not trades:
        return {}

    wins = [t for t in trades if t.get("result") == "WIN"]
    losses = [t for t in trades if t.get("result") == "LOSS"]
    neutrals = [t for t in trades if t.get("result") == "NEUTRAL"]

    total_real = len(wins) + len(losses)
    wr = len(wins) / total_real * 100 if total_real > 0 else 0
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    avg_win = sum(t.get("pnl", 0) for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.get("pnl", 0) for t in losses) / len(losses) if losses else 0

    # Profit factor
    gross_profit = sum(t.get("pnl", 0) for t in wins)
    gross_loss = abs(sum(t.get("pnl", 0) for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "neutrals": len(neutrals),
        "win_rate_pct": round(wr, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
    }


def analyze_by_direction(trades: list[dict]) -> dict:
    """YES vs NO performansı."""
    result = {}
    for direction in ["YES", "NO"]:
        dt = [t for t in trades if t.get("outcome", "").upper() in (
            (["YES", "UP"] if direction == "YES" else ["NO"])
        )]
        wins = sum(1 for t in dt if t.get("result") == "WIN")
        losses = sum(1 for t in dt if t.get("result") == "LOSS")
        pnl = sum(t.get("pnl", 0) for t in dt)
        total = wins + losses
        result[direction] = {
            "trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / total * 100, 1) if total > 0 else 0,
            "pnl": round(pnl, 2),
        }
    return result


def analyze_by_coin(trades: list[dict]) -> dict:
    """Coin bazlı detaylı rapor."""
    coins = defaultdict(lambda: {"wins": 0, "losses": 0, "neutrals": 0, "pnl": 0.0, "trades": []})
    for t in trades:
        coin = detect_coin(t.get("question", ""))
        result = t.get("result", "")
        if result == "WIN":
            coins[coin]["wins"] += 1
        elif result == "LOSS":
            coins[coin]["losses"] += 1
        else:
            coins[coin]["neutrals"] += 1
        coins[coin]["pnl"] += t.get("pnl", 0)
        coins[coin]["trades"].append(t)

    report = {}
    for coin, data in sorted(coins.items()):
        total = data["wins"] + data["losses"]
        wr = data["wins"] / total * 100 if total > 0 else 0

        # YES/NO breakdown
        yes_t = [t for t in data["trades"] if t.get("outcome", "").upper() in ("YES", "UP") and t.get("result") in ("WIN", "LOSS")]
        no_t = [t for t in data["trades"] if t.get("outcome", "").upper() == "NO" and t.get("result") in ("WIN", "LOSS")]
        yes_wr = sum(1 for t in yes_t if t["result"] == "WIN") / len(yes_t) * 100 if yes_t else 0
        no_wr = sum(1 for t in no_t if t["result"] == "WIN") / len(no_t) * 100 if no_t else 0

        report[coin] = {
            "total": total,
            "wins": data["wins"],
            "losses": data["losses"],
            "neutrals": data["neutrals"],
            "win_rate_pct": round(wr, 1),
            "pnl": round(data["pnl"], 2),
            "yes_wr_pct": round(yes_wr, 1),
            "yes_count": len(yes_t),
            "no_wr_pct": round(no_wr, 1),
            "no_count": len(no_t),
            "grade": "A" if wr >= 70 else "B" if wr >= 55 else "C" if wr >= 45 else "F",
        }
    return report


def analyze_by_timeframe(trades: list[dict]) -> dict:
    """Timeframe bazlı YES/NO performans raporu."""
    result = {}
    for tf in ["5m", "15m", "1h", "4h"]:
        tf_trades = [t for t in trades if detect_timeframe(t.get("question", "")) == tf and t.get("result") in ("WIN", "LOSS")]
        if not tf_trades:
            continue

        yes_t = [t for t in tf_trades if t.get("outcome", "").upper() in ("YES", "UP")]
        no_t = [t for t in tf_trades if t.get("outcome", "").upper() == "NO"]

        total_wins = sum(1 for t in tf_trades if t["result"] == "WIN")
        yes_wins = sum(1 for t in yes_t if t["result"] == "WIN")
        no_wins = sum(1 for t in no_t if t["result"] == "WIN")
        pnl = sum(t.get("pnl", 0) for t in tf_trades)

        result[tf] = {
            "total": len(tf_trades),
            "win_rate_pct": round(total_wins / len(tf_trades) * 100, 1),
            "pnl": round(pnl, 2),
            "yes_count": len(yes_t),
            "yes_wr_pct": round(yes_wins / len(yes_t) * 100, 1) if yes_t else 0,
            "no_count": len(no_t),
            "no_wr_pct": round(no_wins / len(no_t) * 100, 1) if no_t else 0,
        }
    return result


def analyze_entry_price_zones(trades: list[dict]) -> list[dict]:
    """Entry price aralıklarına göre WR analizi."""
    buckets = [(0, 0.20), (0.20, 0.35), (0.35, 0.50), (0.50, 0.65), (0.65, 0.80), (0.80, 1.0)]
    zones = []
    for lo, hi in buckets:
        bucket_trades = [t for t in trades if lo <= t.get("entry_price", 0) < hi and t.get("result") in ("WIN", "LOSS")]
        if not bucket_trades:
            continue
        wins = sum(1 for t in bucket_trades if t["result"] == "WIN")
        pnl = sum(t.get("pnl", 0) for t in bucket_trades)
        wr = wins / len(bucket_trades) * 100
        zones.append({
            "range": f"{lo:.2f}-{hi:.2f}",
            "trades": len(bucket_trades),
            "wins": wins,
            "win_rate_pct": round(wr, 1),
            "pnl": round(pnl, 2),
            "verdict": "GOLDZONE" if wr >= 65 else "OK" if wr >= 50 else "DANGER" if wr >= 30 else "TOXIC",
        })
    return zones


def analyze_loss_patterns(trades: list[dict]) -> dict:
    """Loss streak pattern'leri ve tetikleyicileri."""
    streaks = []
    current_streak = 0
    streak_trades = []

    for t in trades:
        if t.get("result") == "LOSS":
            current_streak += 1
            streak_trades.append(t)
        else:
            if current_streak > 0:
                streaks.append({
                    "length": current_streak,
                    "trades": [
                        {"coin": detect_coin(s.get("question", "")),
                         "tf": detect_timeframe(s.get("question", "")),
                         "direction": s.get("outcome", "?"),
                         "entry": s.get("entry_price", 0)}
                        for s in streak_trades
                    ],
                })
            current_streak = 0
            streak_trades = []
    if current_streak > 0:
        streaks.append({"length": current_streak, "trades": [
            {"coin": detect_coin(s.get("question", "")),
             "tf": detect_timeframe(s.get("question", "")),
             "direction": s.get("outcome", "?"),
             "entry": s.get("entry_price", 0)}
            for s in streak_trades
        ]})

    # Streak dağılımı
    dist = Counter(s["length"] for s in streaks)
    max_streak = max((s["length"] for s in streaks), default=0)

    # Streak tetikleyicileri: 3+ streak başlangıç trade'lerinin ortak özellikleri
    triggers = defaultdict(int)
    for s in streaks:
        if s["length"] >= 3 and s["trades"]:
            first = s["trades"][0]
            triggers[f"{first['direction']}_{first['tf']}"] += 1
            triggers[f"coin_{first['coin']}"] += 1

    return {
        "max_streak": max_streak,
        "streak_distribution": dict(sorted(dist.items())),
        "total_streaks": len(streaks),
        "avg_streak_length": round(sum(s["length"] for s in streaks) / len(streaks), 1) if streaks else 0,
        "streak_triggers_3plus": dict(triggers),
        "big_streaks": [s for s in streaks if s["length"] >= 5],
    }


def analyze_trend(trades: list[dict]) -> dict:
    """Son 24h vs önceki dönem karşılaştırması."""
    now = datetime.now()
    today_str = now.strftime("March %d").replace(" 0", " ")
    yesterday = now - timedelta(days=1)
    yesterday_str = yesterday.strftime("March %d").replace(" 0", " ")

    recent = []
    older = []
    for t in trades:
        q = t.get("question", "")
        date_str = detect_date(q)
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                if (now - dt).days <= 1:
                    recent.append(t)
                else:
                    older.append(t)
            except:
                older.append(t)
        else:
            older.append(t)

    def _stats(group):
        real = [t for t in group if t.get("result") in ("WIN", "LOSS")]
        if not real:
            return {"trades": 0, "wr": 0, "pnl": 0}
        wins = sum(1 for t in real if t["result"] == "WIN")
        return {
            "trades": len(real),
            "wr": round(wins / len(real) * 100, 1),
            "pnl": round(sum(t.get("pnl", 0) for t in group), 2),
        }

    recent_stats = _stats(recent)
    older_stats = _stats(older)

    # Trend yönü
    if recent_stats["trades"] < 5:
        trend_direction = "INSUFFICIENT_DATA"
    elif recent_stats["wr"] > older_stats["wr"] + 5:
        trend_direction = "IMPROVING"
    elif recent_stats["wr"] < older_stats["wr"] - 5:
        trend_direction = "DECLINING"
    else:
        trend_direction = "STABLE"

    return {
        "recent_24h": recent_stats,
        "older": older_stats,
        "trend": trend_direction,
    }


def analyze_daily_breakdown(trades: list[dict]) -> list[dict]:
    """Gün bazlı performans — son 7 gün."""
    by_date = defaultdict(list)
    for t in trades:
        date_str = detect_date(t.get("question", ""))
        if date_str:
            by_date[date_str].append(t)

    daily = []
    for date_str in sorted(by_date.keys())[-7:]:
        group = by_date[date_str]
        real = [t for t in group if t.get("result") in ("WIN", "LOSS")]
        if not real:
            continue
        wins = sum(1 for t in real if t["result"] == "WIN")
        pnl = sum(t.get("pnl", 0) for t in group)
        daily.append({
            "date": date_str,
            "trades": len(real),
            "wins": wins,
            "losses": len(real) - wins,
            "wr_pct": round(wins / len(real) * 100, 1),
            "pnl": round(pnl, 2),
        })
    return daily


def generate_rules(
    direction_stats: dict,
    tf_stats: dict,
    coin_stats: dict,
    price_zones: list[dict],
    loss_patterns: dict,
) -> list[dict]:
    """Veriden kanıta dayalı kurallar üret."""
    rules = []

    # Rule 1: NO genel performans
    no = direction_stats.get("NO", {})
    if no.get("trades", 0) >= 20 and no.get("win_rate_pct", 50) < 35:
        rules.append({
            "id": "NO_OVERALL_WEAK",
            "action": f"NO trade'lerde çok dikkatli ol — sadece edge > 0.12 ve 5m timeframe'de izin ver",
            "evidence": f"NO: {no['wins']}W/{no['losses']}L = {no['win_rate_pct']}% WR, ${no['pnl']} PnL",
            "confidence": "HIGH" if no["trades"] >= 50 else "MEDIUM",
            "sample_size": no["trades"],
            "severity": "CRITICAL",
        })

    # Rule 2: Timeframe bazlı bloklar
    for tf, stats in tf_stats.items():
        if stats["total"] >= 10:
            # NO trade timeframe block
            if stats["no_count"] >= 5 and stats["no_wr_pct"] < 35:
                rules.append({
                    "id": f"NO_{tf.upper()}_BLOCK",
                    "action": f"{tf} timeframe'de NO trade yapma",
                    "evidence": f"{tf} NO: {int(stats['no_count'] * stats['no_wr_pct'] / 100)}W/{stats['no_count'] - int(stats['no_count'] * stats['no_wr_pct'] / 100)}L = {stats['no_wr_pct']}% WR",
                    "confidence": "HIGH" if stats["no_count"] >= 20 else "MEDIUM",
                    "sample_size": stats["no_count"],
                    "severity": "HIGH",
                })
            # Genel timeframe uyarısı
            if stats["win_rate_pct"] < 40:
                rules.append({
                    "id": f"TF_{tf.upper()}_WARNING",
                    "action": f"{tf} timeframe'de min edge eşiğini artır (0.12+)",
                    "evidence": f"{tf}: {stats['total']} trade, {stats['win_rate_pct']}% WR, ${stats['pnl']} PnL",
                    "confidence": "HIGH" if stats["total"] >= 30 else "MEDIUM",
                    "sample_size": stats["total"],
                    "severity": "HIGH",
                })

    # Rule 3: Coin bazlı
    for coin, stats in coin_stats.items():
        if stats["total"] >= 10:
            if stats["win_rate_pct"] >= 70:
                rules.append({
                    "id": f"COIN_{coin}_BOOST",
                    "action": f"{coin} trade'lerine öncelik ver — güçlü performans",
                    "evidence": f"{coin}: {stats['wins']}W/{stats['losses']}L = {stats['win_rate_pct']}% WR, ${stats['pnl']} PnL",
                    "confidence": "HIGH" if stats["total"] >= 30 else "MEDIUM",
                    "sample_size": stats["total"],
                    "severity": "INFO",
                })
            elif stats["win_rate_pct"] < 45:
                rules.append({
                    "id": f"COIN_{coin}_CAUTION",
                    "action": f"{coin} trade'lerinde dikkatli ol — düşük WR",
                    "evidence": f"{coin}: {stats['wins']}W/{stats['losses']}L = {stats['win_rate_pct']}% WR, ${stats['pnl']} PnL",
                    "confidence": "HIGH" if stats["total"] >= 30 else "MEDIUM",
                    "sample_size": stats["total"],
                    "severity": "MEDIUM",
                })

    # Rule 4: Entry price zona
    for zone in price_zones:
        if zone["verdict"] == "TOXIC" and zone["trades"] >= 10:
            rules.append({
                "id": f"PRICE_ZONE_TOXIC_{zone['range']}",
                "action": f"Entry price {zone['range']} aralığında trade yapma — toxic zona",
                "evidence": f"Price {zone['range']}: {zone['wins']}W/{zone['trades']-zone['wins']}L = {zone['win_rate_pct']}% WR, ${zone['pnl']} PnL",
                "confidence": "HIGH" if zone["trades"] >= 20 else "MEDIUM",
                "sample_size": zone["trades"],
                "severity": "HIGH",
            })
        elif zone["verdict"] == "GOLDZONE" and zone["trades"] >= 15:
            rules.append({
                "id": f"PRICE_ZONE_GOLD_{zone['range']}",
                "action": f"Entry price {zone['range']} — en iyi zona, Kelly multiplier artır",
                "evidence": f"Price {zone['range']}: {zone['wins']}W/{zone['trades']-zone['wins']}L = {zone['win_rate_pct']}% WR, ${zone['pnl']} PnL",
                "confidence": "HIGH" if zone["trades"] >= 30 else "MEDIUM",
                "sample_size": zone["trades"],
                "severity": "INFO",
            })

    # Rule 5: Loss streak koruması
    if loss_patterns["max_streak"] >= 5:
        triggers = loss_patterns.get("streak_triggers_3plus", {})
        top_trigger = max(triggers.items(), key=lambda x: x[1], default=("?", 0))
        rules.append({
            "id": "LOSS_STREAK_GUARD",
            "action": f"5+ loss streak sonrası 2 döngü bekle. En sık tetikleyici: {top_trigger[0]}",
            "evidence": f"Max streak: {loss_patterns['max_streak']}, ortalama: {loss_patterns['avg_streak_length']}",
            "confidence": "HIGH",
            "sample_size": loss_patterns["total_streaks"],
            "severity": "HIGH",
        })

    # Rule 6: YES 5m altın standart
    tf_5m = tf_stats.get("5m", {})
    if tf_5m and tf_5m.get("yes_wr_pct", 0) >= 70 and tf_5m.get("yes_count", 0) >= 30:
        rules.append({
            "id": "YES_5M_GOLD_STANDARD",
            "action": "5m YES trade'ler en güvenilir — bet size'ı artırabilirsin",
            "evidence": f"5m YES: {tf_5m['yes_count']} trade, {tf_5m['yes_wr_pct']}% WR",
            "confidence": "HIGH",
            "sample_size": tf_5m["yes_count"],
            "severity": "INFO",
        })

    return rules


def generate_warnings(
    summary: dict,
    trend: dict,
    loss_patterns: dict,
    daily: list[dict],
    capital: float,
) -> list[dict]:
    """Aktif uyarılar üret."""
    warnings = []

    # Düşen WR uyarısı
    if trend.get("trend") == "DECLINING":
        warnings.append({
            "type": "WR_DECLINING",
            "message": f"Son 24h WR ({trend['recent_24h']['wr']}%) önceki dönemin ({trend['older']['wr']}%) altında — strateji kontrol et",
            "severity": "HIGH",
        })

    # Sermaye uyarısı
    initial = float(os.environ.get("INITIAL_CAPITAL", 500))
    if capital < initial * 0.1:
        warnings.append({
            "type": "CAPITAL_CRITICAL",
            "message": f"Sermaye ${capital:.2f} — başlangıcın %{capital/initial*100:.0f}'unda. Bot durmalı mı?",
            "severity": "CRITICAL",
        })
    elif capital < initial * 0.3:
        warnings.append({
            "type": "CAPITAL_LOW",
            "message": f"Sermaye ${capital:.2f} — başlangıcın %{capital/initial*100:.0f}'unda",
            "severity": "HIGH",
        })

    # Son gün kayıp mı
    if daily and daily[-1]["pnl"] < -5:
        warnings.append({
            "type": "DAILY_LOSS",
            "message": f"Bugün: ${daily[-1]['pnl']:.2f} PnL ({daily[-1]['wr_pct']}% WR)",
            "severity": "MEDIUM",
        })

    # Aktif loss streak
    if loss_patterns["max_streak"] >= 8:
        warnings.append({
            "type": "MAX_LOSS_STREAK",
            "message": f"Max loss streak {loss_patterns['max_streak']} — circuit breaker eşiğini kontrol et",
            "severity": "HIGH",
        })

    return warnings


def main():
    print("Trade Memory Analyzer başlatılıyor...")

    trades, capital, open_positions = load_trades()
    real_trades = [t for t in trades if t.get("result") in ("WIN", "LOSS")]
    print(f"  {len(trades)} kapalı trade yüklendi ({len(real_trades)} W/L)")

    # Analizler
    summary = analyze_overall(trades)
    summary["capital"] = round(capital, 2)
    summary["open_positions"] = len(open_positions)

    direction = analyze_by_direction(trades)
    coin_report = analyze_by_coin(trades)
    tf_report = analyze_by_timeframe(trades)
    price_zones = analyze_entry_price_zones(trades)
    loss_patterns = analyze_loss_patterns(trades)
    trend = analyze_trend(trades)
    daily = analyze_daily_breakdown(trades)

    # Kurallar ve uyarılar
    rules = generate_rules(direction, tf_report, coin_report, price_zones, loss_patterns)
    warnings = generate_warnings(summary, trend, loss_patterns, daily, capital)

    # Hafıza dosyasını yaz
    memory = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "direction_stats": direction,
        "coin_report": coin_report,
        "timeframe_report": tf_report,
        "entry_price_zones": price_zones,
        "loss_patterns": {
            "max_streak": loss_patterns["max_streak"],
            "avg_streak_length": loss_patterns["avg_streak_length"],
            "total_streaks": loss_patterns["total_streaks"],
            "streak_distribution": loss_patterns["streak_distribution"],
            "streak_triggers": loss_patterns.get("streak_triggers_3plus", {}),
        },
        "trend": trend,
        "daily_breakdown": daily,
        "rules": rules,
        "warnings": warnings,
    }

    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2, ensure_ascii=False)

    # Konsol raporu
    print(f"\n{'='*60}")
    print(f"  TRADE MEMORY RAPORU")
    print(f"{'='*60}")
    print(f"  Toplam: {summary['total_trades']} trade | WR: {summary['win_rate_pct']}% | PnL: ${summary['total_pnl']}")
    print(f"  Sermaye: ${capital:.2f} | Açık: {len(open_positions)}")
    print(f"  YES: {direction['YES']['win_rate_pct']}% WR | NO: {direction['NO']['win_rate_pct']}% WR")
    print()

    print(f"  KURALLAR ({len(rules)} adet):")
    for r in rules:
        icon = "🚫" if r["severity"] in ("CRITICAL", "HIGH") else "⚠️" if r["severity"] == "MEDIUM" else "✅"
        print(f"    {icon} [{r['id']}] {r['action']}")
    print()

    if warnings:
        print(f"  UYARILAR ({len(warnings)} adet):")
        for w in warnings:
            icon = "🔴" if w["severity"] == "CRITICAL" else "🟡" if w["severity"] == "HIGH" else "🟠"
            print(f"    {icon} {w['message']}")
        print()

    print(f"  Hafıza dosyası: {MEMORY_FILE}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
