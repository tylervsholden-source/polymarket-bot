import json

d = json.load(open('data/positions.json'))
positions = d.get('positions', {})
if isinstance(positions, dict):
    pos_list = list(positions.values())
else:
    pos_list = positions

closed = d.get('closed', [])
w = sum(1 for x in closed if isinstance(x, dict) and x.get('result') == 'WIN')
l = sum(1 for x in closed if isinstance(x, dict) and x.get('result') == 'LOSS')
total_pnl = sum(x.get('pnl', 0) for x in closed if isinstance(x, dict))
unrealized = sum(p.get('unrealized_pnl', 0) for p in pos_list)

print(f"Capital: ${d.get('capital', 0):.2f}")
print(f"Open: {len(pos_list)} | Unrealized: ${unrealized:.2f}")
for p in pos_list:
    q = p.get('question', '?')[:55]
    print(f"  {p.get('outcome','?')}@{p.get('entry_price',0)} -> now {p.get('current_price','?')} PnL=${p.get('unrealized_pnl',0):+.2f} | {q}")

print(f"Closed: {len(closed)} | W:{w} L:{l} WR:{w/(w+l)*100:.0f}%")
print(f"Total realized PnL: ${total_pnl:+.2f}")

last5 = [x for x in closed[-5:] if isinstance(x, dict)]
print("Last 5:")
for c in last5:
    print(f"  {c.get('outcome','?')} {c.get('result','?')} ${c.get('pnl',0):+.2f} | {c.get('question','?')[:55]}")
