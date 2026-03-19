"""
Debug script: Fetch raw orderbook for a NO token to investigate
why all NO asks show $0.99.

Does NOT execute any trades. Read-only.
"""
import os
import sys
import json

# Load .env
from pathlib import Path
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from py_clob_client.client import ClobClient
import httpx

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHAIN_ID = 137

def main():
    pk = os.getenv("POLYMARKET_PRIVATE_KEY")
    wallet = os.getenv("POLYMARKET_WALLET_ADDRESS")

    if not pk or not wallet:
        print("ERROR: POLYMARKET_PRIVATE_KEY and POLYMARKET_WALLET_ADDRESS required in .env")
        sys.exit(1)

    # Init CLOB client
    client = ClobClient(
        host=CLOB_HOST,
        key=pk,
        chain_id=CHAIN_ID,
        signature_type=1,
        funder=wallet,
    )
    client.set_api_creds(client.create_or_derive_api_creds())
    print("CLOB client initialized.\n")

    # Find an active crypto up-or-down market via Gamma API
    print("Fetching active crypto up-or-down markets from Gamma API...")
    resp = httpx.get(f"{GAMMA_API}/markets", params={
        "active": "true",
        "closed": "false",
        "limit": 50,
        "order": "endDate",
        "ascending": "true",
    }, timeout=15)

    markets = resp.json()
    crypto_kw = ["bitcoin up or down", "ethereum up or down", "solana up or down",
                 "btc up or down", "eth up or down", "sol up or down"]

    # Find first matching market
    target = None
    for m in markets:
        q = m.get("question", "").lower()
        if any(kw in q for kw in crypto_kw):
            token_ids = m.get("clobTokenIds", [])
            if isinstance(token_ids, str):
                token_ids = json.loads(token_ids)
            if len(token_ids) >= 2:
                target = m
                break

    if not target:
        print("No active crypto up-or-down market found!")
        sys.exit(1)

    question = target["question"]
    token_ids = target.get("clobTokenIds", [])
    if isinstance(token_ids, str):
        token_ids = json.loads(token_ids)

    yes_token = token_ids[0]
    no_token = token_ids[1]

    print(f"Market: {question}")
    print(f"YES token: {yes_token}")
    print(f"NO  token: {no_token}")
    print()

    # Fetch YES orderbook
    print("=" * 60)
    print("YES TOKEN ORDERBOOK")
    print("=" * 60)
    yes_book = client.get_order_book(yes_token)
    print(f"  Asks (count={len(yes_book.asks)}):")
    for i, a in enumerate(yes_book.asks):
        print(f"    [{i}] price={a.price}  size={a.size}")
    print(f"  Bids (count={len(yes_book.bids)}):")
    for i, b in enumerate(yes_book.bids):
        print(f"    [{i}] price={b.price}  size={b.size}")

    print()

    # Fetch NO orderbook
    print("=" * 60)
    print("NO TOKEN ORDERBOOK")
    print("=" * 60)
    no_book = client.get_order_book(no_token)
    print(f"  Asks (count={len(no_book.asks)}):")
    for i, a in enumerate(no_book.asks):
        print(f"    [{i}] price={a.price}  size={a.size}")
    print(f"  Bids (count={len(no_book.bids)}):")
    for i, b in enumerate(no_book.bids):
        print(f"    [{i}] price={b.price}  size={b.size}")

    print()

    # Show what our bot's get_orderbook() would return
    print("=" * 60)
    print("WHAT OUR BOT SEES (polymarket_client.get_orderbook logic)")
    print("=" * 60)

    # YES
    yes_asks = yes_book.asks
    yes_bids = yes_book.bids
    yes_best_ask = float(yes_asks[0].price) if yes_asks else 0.0
    yes_best_bid = float(yes_bids[-1].price) if yes_bids else 0.0
    print(f"  YES: best_ask=asks[0]={yes_best_ask}  best_bid=bids[-1]={yes_best_bid}")

    # What it SHOULD be if asks ascending, bids descending:
    if yes_asks:
        ask_prices = [float(a.price) for a in yes_asks]
        print(f"  YES asks min={min(ask_prices)} max={max(ask_prices)}")
        print(f"  YES asks[0]={ask_prices[0]} (first) asks[-1]={ask_prices[-1]} (last)")
        if ask_prices == sorted(ask_prices):
            print(f"  YES asks are sorted ASCENDING (lowest first) -> asks[0] IS best ask")
        elif ask_prices == sorted(ask_prices, reverse=True):
            print(f"  YES asks are sorted DESCENDING (highest first) -> asks[0] is WORST ask!")
        else:
            print(f"  YES asks are NOT sorted!")

    if yes_bids:
        bid_prices = [float(b.price) for b in yes_bids]
        print(f"  YES bids min={min(bid_prices)} max={max(bid_prices)}")
        print(f"  YES bids[0]={bid_prices[0]} (first) bids[-1]={bid_prices[-1]} (last)")
        if bid_prices == sorted(bid_prices, reverse=True):
            print(f"  YES bids are sorted DESCENDING (highest first) -> bids[0] IS best bid, bids[-1] is WORST")
        elif bid_prices == sorted(bid_prices):
            print(f"  YES bids are sorted ASCENDING (lowest first) -> bids[-1] IS best bid")
        else:
            print(f"  YES bids are NOT sorted!")

    print()

    # NO
    no_asks = no_book.asks
    no_bids = no_book.bids
    no_best_ask = float(no_asks[0].price) if no_asks else 0.0
    no_best_bid = float(no_bids[-1].price) if no_bids else 0.0
    print(f"  NO:  best_ask=asks[0]={no_best_ask}  best_bid=bids[-1]={no_best_bid}")

    if no_asks:
        ask_prices = [float(a.price) for a in no_asks]
        print(f"  NO asks min={min(ask_prices)} max={max(ask_prices)}")
        print(f"  NO asks[0]={ask_prices[0]} (first) asks[-1]={ask_prices[-1]} (last)")
        if ask_prices == sorted(ask_prices):
            print(f"  NO asks are sorted ASCENDING (lowest first) -> asks[0] IS best ask")
        elif ask_prices == sorted(ask_prices, reverse=True):
            print(f"  NO asks are sorted DESCENDING (highest first) -> asks[0] is WORST ask!")
        else:
            print(f"  NO asks are NOT sorted!")

    if no_bids:
        bid_prices = [float(b.price) for b in no_bids]
        print(f"  NO bids min={min(bid_prices)} max={max(bid_prices)}")
        print(f"  NO bids[0]={bid_prices[0]} (first) bids[-1]={bid_prices[-1]} (last)")
        if bid_prices == sorted(bid_prices, reverse=True):
            print(f"  NO bids are sorted DESCENDING (highest first) -> bids[0] IS best bid, bids[-1] is WORST")
        elif bid_prices == sorted(bid_prices):
            print(f"  NO bids are sorted ASCENDING (lowest first) -> bids[-1] IS best bid")
        else:
            print(f"  NO bids are NOT sorted!")

    print()
    print("=" * 60)
    print("CORRECT VALUES (using min/max)")
    print("=" * 60)
    if yes_asks:
        print(f"  YES best ask (lowest):  {min(float(a.price) for a in yes_asks)}")
    if yes_bids:
        print(f"  YES best bid (highest): {max(float(b.price) for b in yes_bids)}")
    if no_asks:
        print(f"  NO  best ask (lowest):  {min(float(a.price) for a in no_asks)}")
    if no_bids:
        print(f"  NO  best bid (highest): {max(float(b.price) for b in no_bids)}")


if __name__ == "__main__":
    main()
