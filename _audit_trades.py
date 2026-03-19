"""Full on-chain trade audit — pulls ALL trades from Polymarket data API."""
import asyncio
import httpx
import os
import json
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()


async def main():
    wallet = os.getenv("POLYMARKET_WALLET_ADDRESS")
    if not wallet:
        print("NO WALLET ADDRESS in .env")
        return

    session = httpx.AsyncClient(timeout=30)

    # 1. Get ALL trades
    all_trades = []
    offset = 0
    while True:
        resp = await session.get(
            "https://data-api.polymarket.com/trades",
            params={"user": wallet, "limit": 100, "offset": offset},
        )
        if resp.status_code != 200:
            print(f"API error: {resp.status_code}")
            break
        batch = resp.json()
        if not batch:
            break
        all_trades.extend(batch)
        offset += 100
        if len(batch) < 100:
            break

    print(f"Total on-chain trades: {len(all_trades)}")

    if not all_trades:
        # Try alternative endpoint
        resp = await session.get(
            "https://data-api.polymarket.com/activity",
            params={"user": wallet, "limit": 500},
        )
        if resp.status_code == 200:
            all_trades = resp.json() if isinstance(resp.json(), list) else []
            print(f"Activity trades: {len(all_trades)}")

    if not all_trades:
        print("No trades found via API. Checking positions endpoint...")
        resp = await session.get(
            "https://data-api.polymarket.com/positions",
            params={"user": wallet, "limit": 200},
        )
        if resp.status_code == 200:
            positions = resp.json() if isinstance(resp.json(), list) else []
            print(f"On-chain positions: {len(positions)}")

            total_invested = 0
            total_current = 0
            total_pnl = 0
            win = 0
            loss = 0
            by_outcome = defaultdict(list)

            for p in positions:
                cur_val = float(p.get("currentValue", 0) or 0)
                init_val = float(p.get("initialValue", 0) or 0)
                pnl = float(p.get("pnl", 0) or 0)
                realized = float(p.get("realizedPnl", 0) or 0)
                size = float(p.get("size", 0) or 0)
                avg_price = float(p.get("avgPrice", 0) or 0)
                title = p.get("title", p.get("market", ""))[:60]
                outcome = p.get("outcome", "?")

                total_invested += init_val
                total_current += cur_val
                total_pnl += pnl

                if pnl > 0:
                    win += 1
                elif pnl < 0:
                    loss += 1

                by_outcome[outcome].append(p)

                # Print non-zero positions
                if abs(pnl) > 0.01 or cur_val > 0.01:
                    print(
                        f"  {outcome:4s} | init={init_val:7.2f} cur={cur_val:7.2f} "
                        f"pnl={pnl:+7.2f} real={realized:+7.2f} | {title}"
                    )

            print()
            print(f"Total positions: {len(positions)}")
            print(f"Total invested: ${total_invested:.2f}")
            print(f"Total current value: ${total_current:.2f}")
            print(f"Total PnL: ${total_pnl:+.2f}")
            print(f"Wins: {win} | Losses: {loss} | Neutral: {len(positions) - win - loss}")
            print(f"Win rate: {win / max(1, win + loss) * 100:.1f}%")

            print()
            print("=== BY OUTCOME ===")
            for outcome, pos_list in by_outcome.items():
                total_p = sum(float(p.get("pnl", 0) or 0) for p in pos_list)
                w = sum(1 for p in pos_list if float(p.get("pnl", 0) or 0) > 0)
                l = sum(1 for p in pos_list if float(p.get("pnl", 0) or 0) < 0)
                print(f"  {outcome}: {len(pos_list)} positions, PnL=${total_p:+.2f}, W:{w} L:{l}")

            # Save raw data
            with open("artifacts/positions_live.json", "w") as f:
                json.dump(positions, f, indent=2)
            print("\nSaved to artifacts/positions_live.json")
        return

    # Process trades
    total_buy = 0
    total_sell = 0
    buy_count = 0
    sell_count = 0

    for t in all_trades:
        side = t.get("side", "")
        price = float(t.get("price", 0))
        size = float(t.get("size", 0))
        cost = price * size

        if side == "BUY":
            total_buy += cost
            buy_count += 1
        elif side == "SELL":
            total_sell += cost
            sell_count += 1

    print(f"BUY trades: {buy_count} (total cost: ${total_buy:.2f})")
    print(f"SELL trades: {sell_count} (total revenue: ${total_sell:.2f})")
    print(f"Net from trading: ${total_sell - total_buy:+.2f}")

    # Save raw data
    with open("artifacts/recent_trades.json", "w") as f:
        json.dump(all_trades[:50], f, indent=2)
    print("\nSaved sample to artifacts/recent_trades.json")


if __name__ == "__main__":
    asyncio.run(main())
