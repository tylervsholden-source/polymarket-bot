
import json
from pathlib import Path

def analyze():
    pos_file = Path("data/positions.json")
    if not pos_file.exists():
        print("positions.json not found")
        return

    with open(pos_file) as f:
        data = json.load(f)

    capital = data.get("capital", 0)
    closed = data.get("closed", [])
    
    total_pnl = 0
    wins = 0
    losses = 0
    neutrals = 0
    
    for trade in closed:
        pnl = trade.get("pnl", 0)
        total_pnl += pnl
        result = trade.get("result", "")
        if result == "WIN": wins += 1
        elif result == "LOSS": losses += 1
        else: neutrals += 1

    print(f"--- positions.json Analysis ---")
    print(f"Current Capital (logged): ${capital:.6f}")
    print(f"Total Trades: {len(closed)}")
    print(f"Wins: {wins}, Losses: {losses}, Neutrals: {neutrals}")
    print(f"Cumulative PnL from trades: ${total_pnl:.6f}")
    
    if len(closed) > 0:
        first_trade_date = closed[0].get("question", "Unknown")
        last_trade_date = closed[-1].get("question", "Unknown")
        print(f"First trade: {first_trade_date}")
        print(f"Last trade: {last_trade_date}")

analyze()
