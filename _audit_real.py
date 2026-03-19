"""Real P&L audit from Polymarket API."""
import os, httpx, json
from dotenv import load_dotenv
load_dotenv()

wallet = os.getenv('POLYMARKET_WALLET_ADDRESS', '')

all_data = []
for offset in [0, 200, 400]:
    r = httpx.get('https://data-api.polymarket.com/activity',
                  params={'user': wallet, 'limit': '200', 'offset': str(offset)}, timeout=15)
    if r.status_code == 200:
        batch = r.json()
        if not batch:
            break
        all_data.extend(batch)

trades = [d for d in all_data if d['type'] == 'TRADE']
redeems = [d for d in all_data if d['type'] == 'REDEEM']

total_spent = sum(t['price'] * t['size'] for t in trades if t.get('price', 0) > 0 and t.get('size', 0) > 0)
total_redeemed = sum(t.get('size', 0) for t in redeems if t.get('size', 0) > 0)

print(f"Total trades: {len(trades)}")
print(f"Total redeems: {len(redeems)}")
print(f"Total spent: ${total_spent:.2f}")
print(f"Total redeemed (shares at $1): ${total_redeemed:.2f}")
print(f"Net P&L: ${total_redeemed - total_spent:.2f}")
print(f"Current balance: $2.78")

# Open positions
r2 = httpx.get('https://data-api.polymarket.com/positions',
               params={'user': wallet, 'sizeThreshold': '0.001', 'limit': '500'}, timeout=15)
if r2.status_code == 200:
    positions = r2.json()
    print(f"\nOpen positions: {len(positions)}")
    total_value = 0
    for p in positions[:20]:
        cv = float(p.get('currentValue', 0) or 0)
        sz = float(p.get('size', 0) or 0)
        total_value += cv
        title = p.get('title', '')[:50]
        print(f"  shares={sz:.2f} value=${cv:.4f} | {title}")
    print(f"Total position value: ${total_value:.2f}")
