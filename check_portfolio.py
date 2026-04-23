"""Check real Polymarket portfolio value."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

wallet = os.getenv('POLYMARKET_WALLET_ADDRESS', '')
print(f'Wallet: {wallet[:10]}...' if wallet else 'Wallet: NOT SET')

# 1. CLOB balance (free USDC)
from core.polymarket_client import PolymarketClient
client = PolymarketClient()
balance = client.get_real_balance()
print(f'CLOB free USDC: ${balance:.4f}')

# 2. Open positions (shares held)
import httpx
resp = httpx.get(
    'https://data-api.polymarket.com/positions',
    params={'user': wallet, 'sizeThreshold': '0.001', 'limit': '500'},
    timeout=15,
)
positions = resp.json() if resp.status_code == 200 else []
total_value = 0
print(f'Open positions: {len(positions)}')
for p in positions:
    size = float(p.get('size', 0) or 0)
    avg = float(p.get('avgPrice', 0) or p.get('avg_price', 0) or 0)
    cur = float(p.get('curPrice', 0) or p.get('cur_price', 0) or 0)
    title = (p.get('title') or 'Unknown')[:50]
    outcome = p.get('outcome', '?')
    value = size * cur if cur > 0 else size * avg
    total_value += value
    if size > 0.01:
        print(f'  {outcome} {title} | size={size:.2f} avg={avg:.3f} cur={cur:.3f} val=${value:.2f}')

print('---')
print(f'Free USDC:      ${balance:.2f}')
print(f'Position value:  ${total_value:.2f}')
print(f'TOTAL PORTFOLIO: ${balance + total_value:.2f}')
