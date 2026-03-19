"""Complete financial audit — all on-chain trades analyzed."""
import asyncio
import httpx
import os
import json
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()


async def main():
    wallet = os.getenv("POLYMARKET_WALLET_ADDRESS")
    session = httpx.AsyncClient(timeout=30)

    # Pull ALL trades
    all_trades = []
    offset = 0
    while True:
        resp = await session.get(
            "https://data-api.polymarket.com/trades",
            params={"user": wallet, "limit": 100, "offset": offset},
        )
        if resp.status_code != 200:
            break
        batch = resp.json()
        if not batch:
            break
        all_trades.extend(batch)
        offset += 100
        if len(batch) < 100:
            break

    print(f"Total on-chain trades: {len(all_trades)}")
    print()

    # Group by market (conditionId + outcome)
    by_market = defaultdict(list)
    for t in all_trades:
        key = t.get("conditionId", "") + "|" + t.get("outcome", "")
        by_market[key].append(t)

    # For each market position: total bought, total sold
    positions = []
    for key, trades in by_market.items():
        title = trades[0].get("title", "?")
        outcome = trades[0].get("outcome", "?")

        total_shares_bought = 0
        total_cost = 0
        total_shares_sold = 0
        total_revenue = 0

        for t in trades:
            side = t.get("side", "")
            size = float(t.get("size", 0))
            price = float(t.get("price", 0))

            if side == "BUY":
                total_shares_bought += size
                total_cost += price * size
            elif side == "SELL":
                total_shares_sold += size
                total_revenue += price * size

        net_shares = total_shares_bought - total_shares_sold

        positions.append({
            "title": title,
            "outcome": outcome,
            "buys": sum(1 for t in trades if t.get("side") == "BUY"),
            "sells": sum(1 for t in trades if t.get("side") == "SELL"),
            "shares_bought": round(total_shares_bought, 4),
            "cost": round(total_cost, 4),
            "shares_sold": round(total_shares_sold, 4),
            "revenue": round(total_revenue, 4),
            "net_shares": round(net_shares, 4),
            "avg_buy_price": round(total_cost / total_shares_bought, 4) if total_shares_bought > 0 else 0,
        })

    # Sort by title
    positions.sort(key=lambda x: x["title"])

    # Analyze results
    # For crypto up/down markets that have settled:
    # If you hold UP shares and market went UP -> each share pays $1
    # If you hold DOWN shares and market went DOWN -> each share pays $1
    # Otherwise -> $0
    # Since all these markets have settled, net_shares > 0 means you held to expiry

    total_cost = 0
    total_revenue_from_sells = 0
    total_settlement_payout = 0  # theoretical if all held to expiry

    win_count = 0
    loss_count = 0
    total_won = 0
    total_lost = 0

    up_positions = []
    down_positions = []

    print("=" * 80)
    print("POSITION-BY-POSITION ANALYSIS")
    print("=" * 80)

    for p in positions:
        title = p["title"]
        outcome = p["outcome"]
        cost = p["cost"]
        revenue = p["revenue"]
        net_shares = p["net_shares"]

        total_cost += cost
        total_revenue_from_sells += revenue

        # Did this position win or lose?
        # We can't know for sure without market resolution data,
        # but we can check: if shares were held (not sold) and payout was $0,
        # it means the position lost

        # For now, flag positions by type
        if "Up or Down" in title:
            if outcome == "Up":
                up_positions.append(p)
            else:
                down_positions.append(p)

    # Print summary
    print(f"\nTotal unique market positions: {len(positions)}")
    print(f"  UP positions: {len(up_positions)}")
    print(f"  DOWN positions: {len(down_positions)}")
    print(f"\nTotal money spent (buys): ${total_cost:.2f}")
    print(f"Total money received (sells): ${total_revenue_from_sells:.2f}")
    print(f"Net cash flow from trading: ${total_revenue_from_sells - total_cost:+.2f}")
    print(f"\nNote: Settlement payouts (wins) are NOT captured in trades API.")
    print(f"Settlement happens on-chain, not as a 'sell' trade.")

    # Print each position
    print()
    print("=" * 80)
    print("UP POSITIONS")
    print("=" * 80)
    total_up_cost = 0
    for p in sorted(up_positions, key=lambda x: x["title"]):
        total_up_cost += p["cost"]
        held = "HELD" if p["net_shares"] > 0.1 else "SOLD"
        print(
            f"  {p['title'][:55]:55s} | cost=${p['cost']:6.2f} "
            f"shares={p['shares_bought']:7.2f} avg={p['avg_buy_price']:.3f} "
            f"net={p['net_shares']:7.2f} [{held}]"
        )

    print()
    print("=" * 80)
    print("DOWN POSITIONS")
    print("=" * 80)
    total_down_cost = 0
    for p in sorted(down_positions, key=lambda x: x["title"]):
        total_down_cost += p["cost"]
        held = "HELD" if p["net_shares"] > 0.1 else "SOLD"
        print(
            f"  {p['title'][:55]:55s} | cost=${p['cost']:6.2f} "
            f"shares={p['shares_bought']:7.2f} avg={p['avg_buy_price']:.3f} "
            f"net={p['net_shares']:7.2f} [{held}]"
        )

    print()
    print("=" * 80)
    print("FINANCIAL SUMMARY")
    print("=" * 80)
    print(f"Total UP cost:   ${total_up_cost:.2f}")
    print(f"Total DOWN cost: ${total_down_cost:.2f}")
    print(f"Total all cost:  ${total_cost:.2f}")
    print(f"Sell revenue:    ${total_revenue_from_sells:.2f}")

    # Non-crypto positions
    other = [p for p in positions if "Up or Down" not in p["title"]]
    if other:
        print()
        print("OTHER (non-crypto) positions:")
        for p in other:
            print(f"  {p['title'][:55]:55s} | cost=${p['cost']:6.2f}")

    # Save full analysis
    with open("artifacts/full_audit.json", "w") as f:
        json.dump(positions, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
