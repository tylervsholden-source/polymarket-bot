"""Final audit summary — the real numbers."""
import json
from collections import defaultdict

data = json.load(open("artifacts/full_audit.json"))
pj = json.load(open("data/positions.json"))

# Separate crypto up/down from other bets
crypto = [p for p in data if "Up or Down" in p.get("title", "")]
other = [p for p in data if "Up or Down" not in p.get("title", "")]

up = [p for p in crypto if p["outcome"] == "Up"]
down = [p for p in crypto if p["outcome"] == "Down"]

up_cost = sum(p["cost"] for p in up)
down_cost = sum(p["cost"] for p in down)
other_cost = sum(p["cost"] for p in other)

print("=" * 60)
print("GERCEK ON-CHAIN FINANSAL OZET")
print("=" * 60)
print(f"Toplam market pozisyonu: {len(data)}")
print(f"  Crypto UP:   {len(up):3d} pozisyon, maliyet: ${up_cost:.2f}")
print(f"  Crypto DOWN: {len(down):3d} pozisyon, maliyet: ${down_cost:.2f}")
print(f"  Diger bahis: {len(other):3d} pozisyon, maliyet: ${other_cost:.2f}")
total_all_cost = up_cost + down_cost + other_cost
print(f"  TOPLAM:      {len(data):3d} pozisyon, maliyet: ${total_all_cost:.2f}")
print()

# By date
by_date = defaultdict(lambda: {"up": 0, "down": 0, "up_cost": 0.0, "down_cost": 0.0})
for p in crypto:
    title = p.get("title", "")
    date = "Unknown"
    for d in ["March 13", "March 14", "March 15", "March 16", "March 17", "March 18"]:
        if d in title:
            date = d
            break
    if p["outcome"] == "Up":
        by_date[date]["up"] += 1
        by_date[date]["up_cost"] += p["cost"]
    else:
        by_date[date]["down"] += 1
        by_date[date]["down_cost"] += p["cost"]

print("=" * 60)
print("TARIHE GORE CRYPTO POZISYONLAR")
print("=" * 60)
for date in sorted(by_date.keys()):
    d = by_date[date]
    total_d = d["up"] + d["down"]
    total_c = d["up_cost"] + d["down_cost"]
    print(
        f"  {date:10s}: {total_d:3d} poz "
        f"(UP:{d['up']:2d} DOWN:{d['down']:2d}) | "
        f"${total_c:7.2f} "
        f"(UP:${d['up_cost']:7.2f} DOWN:${d['down_cost']:7.2f})"
    )

# positions.json comparison
tracked = len(pj.get("closed", []))
print()
print("=" * 60)
print("positions.json vs ON-CHAIN KARSILASTIRMA")
print("=" * 60)
print(f"positions.json tracked:  {tracked} trade")
print(f"On-chain crypto:         {len(crypto)} pozisyon ({len(up)} UP + {len(down)} DOWN)")
print(f"On-chain diger bahis:    {len(other)} pozisyon")
print(f"KAYIP (untracked):       {len(data) - tracked} pozisyon!")
print()

# Settlement calculation
# wallet_end = wallet_start + sell_revenue - buy_cost + settlement_payouts
# settlement_payouts = wallet_end - wallet_start - sell_revenue + buy_cost
WALLET_START = 1000.0
WALLET_END = 1.20
SELL_REVENUE = 157.03
BUY_COST = 1256.32

settlement = WALLET_END - WALLET_START - SELL_REVENUE + BUY_COST

print("=" * 60)
print("SETTLEMENT (KAZANC) HESABI")
print("=" * 60)
print(f"  Baslangic:      ${WALLET_START:.2f}")
print(f"  Alislar:        -${BUY_COST:.2f}")
print(f"  Satislar:       +${SELL_REVENUE:.2f}")
print(f"  Settlement:     +${settlement:.2f}")
print(f"  Wallet simdi:   ${WALLET_END:.2f}")
print()

real_pnl = WALLET_END - WALLET_START
print(f"  GERCEK P&L:     ${real_pnl:+.2f}")
print()

# Win rate: how much of what was spent came back?
crypto_total = up_cost + down_cost
payout_ratio = settlement / BUY_COST * 100 if BUY_COST > 0 else 0

print("=" * 60)
print("GERCEK WIN RATE ANALIZI")
print("=" * 60)
print(f"  Toplam harcanan:    ${BUY_COST:.2f}")
print(f"  Geri donen:         ${settlement + SELL_REVENUE:.2f}")
print(f"  Geri donus orani:   {(settlement + SELL_REVENUE) / BUY_COST * 100:.1f}%")
print(f"  NET KAYIP:          ${real_pnl:+.2f} ({real_pnl / WALLET_START * 100:.1f}%)")
print()

# What positions.json THOUGHT vs reality
pj_pnl = sum(t.get("pnl", 0) for t in pj.get("closed", []))
print("=" * 60)
print("POSITIONS.JSON YANILGISI")
print("=" * 60)
print(f"  positions.json diyor: +${pj_pnl:.2f} PnL (85.3% WR)")
print(f"  Gercek on-chain:      -${abs(real_pnl):.2f} PnL")
print(f"  FARK:                 ${real_pnl - pj_pnl:.2f}")
print(f"  Neden: {len(data) - tracked} pozisyon TAKIP EDILMEDI!")
print()

# Estimate real win rate
# We know: 162 UP + 57 DOWN crypto positions + 61 other
# Settlement = $100.49 (from won positions)
# For crypto: avg UP bet ~ up_cost/len(up), avg DOWN bet ~ down_cost/len(down)
avg_up = up_cost / len(up) if up else 0
avg_down = down_cost / len(down) if down else 0
avg_up_payout = avg_up / 0.50 if avg_up > 0 else 0  # rough: buy at ~0.50, payout is 1.0

# How many UP positions won to generate ~$100 in settlement?
# Each UP win pays: shares * 1.0, where shares = cost / avg_price
# We need to work backwards from settlement amount
print("=" * 60)
print("WIN RATE TAHMINI")
print("=" * 60)
# Total crypto positions: 219
# $100 settlement from 219 positions worth $1140
# If avg payout per win is ~$10 (buy at ~0.50, get $1 per share, 10 shares = $10)
# Then roughly 10 positions won out of 219
est_wins = int(settlement / 10)  # rough estimate
print(f"  Tahmini kazanan pozisyon: ~{est_wins} / {len(crypto)} crypto")
print(f"  Tahmini win rate: ~{est_wins / len(crypto) * 100:.0f}%")
print()
print("NOT: Settlement hesabi yaklasiktir.")
print("Kesin sonuc icin her marketin resolution sonucunu")
print("kontrol etmek gerekir.")
