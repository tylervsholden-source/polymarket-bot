#!/usr/bin/env python3
"""
Trade Resolution Notifier
Her çalıştığında positions.json'daki yeni kapanan trade'leri tespit eder
ve rapor olarak çıktı verir.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
POSITIONS_FILE = DATA_DIR / "positions.json"
TRACKER_FILE = DATA_DIR / "last_notified_trade.json"
MEMORY_FILE = DATA_DIR / "trade_memory.json"


def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def parse_coin(question: str) -> str:
    """Extract coin name from question like 'Bitcoin Up or Down - March 22...'"""
    coin_map = {
        "Bitcoin": "BTC", "Ethereum": "ETH", "Solana": "SOL",
        "XRP": "XRP", "Dogecoin": "DOGE", "BNB": "BNB",
        "Hyperliquid": "HYPE"
    }
    for name, ticker in coin_map.items():
        if name.lower() in question.lower():
            return ticker
    return question.split(" ")[0][:6].upper()


def parse_timeframe(question: str) -> str:
    """Extract timeframe from question timestamps"""
    import re
    times = re.findall(r'(\d{1,2}:\d{2}[AP]M)', question)
    if len(times) >= 2:
        try:
            t1 = datetime.strptime(times[0], "%I:%M%p")
            t2 = datetime.strptime(times[1], "%I:%M%p")
            diff_min = int((t2 - t1).total_seconds() / 60)
            if diff_min <= 0:
                diff_min += 720
            return f"{diff_min}m"
        except ValueError:
            pass
    return "?m"


def main():
    positions = load_json(POSITIONS_FILE)
    if not positions:
        print("ERROR: positions.json okunamadı")
        return

    tracker = load_json(TRACKER_FILE) or {
        "last_notified_ts": "2000-01-01T00:00:00+00:00",
        "last_notified_count": 0,
        "notified_order_ids": []
    }

    closed = positions.get("closed", [])
    capital = positions.get("capital", 0)
    open_positions = positions.get("positions", {})
    last_ts = tracker.get("last_notified_ts", "2000-01-01T00:00:00+00:00")
    notified_ids = set(tracker.get("notified_order_ids", []))

    # Find new closed trades since last notification
    new_trades = []
    for trade in closed:
        resolved_at = trade.get("resolved_at", "")
        order_id = trade.get("order_id", "")
        if not resolved_at:
            continue
        if resolved_at > last_ts and order_id not in notified_ids:
            new_trades.append(trade)

    # Sort by resolved_at
    new_trades.sort(key=lambda t: t.get("resolved_at", ""))

    # Load memory for context
    memory = load_json(MEMORY_FILE)

    # === OUTPUT ===

    if not new_trades and not open_positions:
        print(f"NO_NEW_TRADES")
        print(f"Capital: ${capital:.2f} | Açık pozisyon: 0 | Son kontrol: {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
        return

    # Report open positions even if no new closes
    if not new_trades and open_positions:
        print("OPEN_POSITIONS_ONLY")
        print(f"\n--- Açık Pozisyonlar ({len(open_positions)} adet) ---")
        total_exposure = 0
        total_unrealized = 0
        for mid, pos in open_positions.items():
            coin = parse_coin(pos.get("question", ""))
            direction = pos.get("outcome", "?")
            amount = pos.get("amount", 0)
            entry = pos.get("entry_price", 0)
            unrealized = pos.get("unrealized_pnl", 0)
            total_exposure += amount
            total_unrealized += unrealized
            sign = "+" if unrealized >= 0 else ""
            print(f"  {coin} {direction} @ {entry:.2f} | ${amount:.2f} | PnL: {sign}${unrealized:.2f}")
        print(f"\nToplam: ${total_exposure:.2f} exposure | {'+' if total_unrealized >= 0 else ''}${total_unrealized:.2f} unrealized")
        print(f"Capital: ${capital:.2f}")
        return

    # === NEW TRADES FOUND ===
    print("NEW_TRADES_RESOLVED")
    print(f"\n{'='*50}")
    print(f"  TRADE RESOLUTION RAPORU")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*50}")

    session_pnl = 0
    wins = 0
    losses = 0

    for trade in new_trades:
        coin = parse_coin(trade.get("question", ""))
        direction = trade.get("outcome", "?")
        result = trade.get("result", "?")
        pnl = trade.get("pnl", 0)
        amount = trade.get("amount", 0)
        entry = trade.get("entry_price", 0)
        close = trade.get("close_price", 0)
        resolved = trade.get("resolved_at", "")[:19]
        timeframe = parse_timeframe(trade.get("question", ""))

        session_pnl += pnl
        if result == "WIN":
            wins += 1
            icon = "W"
        elif result == "LOSS":
            losses += 1
            icon = "L"
        else:
            icon = "N"

        sign = "+" if pnl >= 0 else ""
        print(f"\n  [{icon}] {coin} {direction} {timeframe}")
        print(f"      Entry: {entry:.2f} | Close: {close:.2f} | Amount: ${amount:.2f}")
        print(f"      PnL: {sign}${pnl:.2f}")

    print(f"\n{'─'*50}")
    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0
    sign = "+" if session_pnl >= 0 else ""
    print(f"  Bu batch: {wins}W / {losses}L ({wr:.0f}% WR) | {sign}${session_pnl:.2f}")
    print(f"  Capital: ${capital:.2f}")

    # Open positions summary
    if open_positions:
        print(f"\n  Açık pozisyon: {len(open_positions)}")
        for mid, pos in open_positions.items():
            coin = parse_coin(pos.get("question", ""))
            direction = pos.get("outcome", "?")
            unrealized = pos.get("unrealized_pnl", 0)
            s = "+" if unrealized >= 0 else ""
            print(f"    - {coin} {direction} | {s}${unrealized:.2f}")

    # Lifetime context from memory
    if memory and "summary" in memory:
        s = memory["summary"]
        print(f"\n  Lifetime: {s.get('total_trades',0)} trade | {s.get('win_rate_pct',0):.1f}% WR | +${s.get('total_pnl',0):.2f}")

    # Warnings
    if memory and "warnings" in memory:
        high_warnings = [w for w in memory["warnings"] if w.get("severity") in ("HIGH", "CRITICAL")]
        if high_warnings:
            print(f"\n  Uyarılar:")
            for w in high_warnings[:3]:
                print(f"    [{w.get('severity')}] {w.get('message','')}")

    print(f"{'='*50}")

    # Update tracker
    if new_trades:
        latest_ts = max(t.get("resolved_at", "") for t in new_trades)
        new_notified_ids = list(notified_ids | {t.get("order_id", "") for t in new_trades})
        # Keep only last 500 IDs to prevent file bloat
        if len(new_notified_ids) > 500:
            new_notified_ids = new_notified_ids[-500:]
        save_json(TRACKER_FILE, {
            "last_notified_ts": latest_ts,
            "last_notified_count": len(new_trades),
            "total_notified": tracker.get("total_notified", 0) + len(new_trades),
            "notified_order_ids": new_notified_ids,
            "updated_at": datetime.now(timezone.utc).isoformat()
        })


if __name__ == "__main__":
    main()
