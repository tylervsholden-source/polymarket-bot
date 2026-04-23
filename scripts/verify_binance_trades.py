#!/usr/bin/env python3
"""
Verify Binance-resolved trades against Polymarket CLOB API.

13 trades were resolved by Binance kline comparison instead of CLOB tokens.winner.
The HYPE 9:50-9:55 trade (#445) was confirmed WRONG by user.
This script checks the remaining 12 against CLOB to find other errors.

Usage: python3 scripts/verify_binance_trades.py
"""

import json
import requests
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
POSITIONS_FILE = DATA_DIR / "positions.json"


def main():
    with open(POSITIONS_FILE) as f:
        data = json.load(f)

    closed = data.get("closed", [])

    # Find Binance-resolved trades (pnl_verified=True, not already corrected)
    binance_trades = []
    for i, t in enumerate(closed):
        if t.get("pnl_verified") and not t.get("resolution_corrected"):
            binance_trades.append((i, t))

    print(f"Found {len(binance_trades)} Binance-resolved trades to verify\n")

    corrections_needed = []
    verified_correct = []
    unable_to_verify = []

    for idx, (i, t) in enumerate(binance_trades):
        token_id = t.get("token_id", "")
        outcome = t.get("outcome", "")
        result = t.get("result", "")
        pnl = t.get("pnl", 0)
        amount = t.get("amount", 0)
        entry = t.get("entry_price", 0)
        q = t.get("question", "")[:60]

        if not token_id:
            print(f"#{i}: NO TOKEN_ID — cannot verify | {q}")
            unable_to_verify.append((i, t))
            continue

        # Query CLOB API for market info using token_id
        # We need to find the market by token_id
        try:
            # Try Gamma API to find market by token
            resp = requests.get(
                f"https://gamma-api.polymarket.com/markets?token_id={token_id}",
                timeout=10
            )
            if resp.status_code != 200:
                print(f"#{i}: Gamma API error {resp.status_code} | {q}")
                unable_to_verify.append((i, t))
                time.sleep(0.5)
                continue

            markets = resp.json()
            if not markets:
                # Try CLOB directly
                resp2 = requests.get(
                    f"https://clob.polymarket.com/markets?token_id={token_id}",
                    timeout=10
                )
                if resp2.status_code == 200:
                    markets = [resp2.json()] if isinstance(resp2.json(), dict) else resp2.json()

            if not markets:
                print(f"#{i}: No market found for token | {q}")
                unable_to_verify.append((i, t))
                time.sleep(0.5)
                continue

            market = markets[0] if isinstance(markets, list) else markets
            condition_id = market.get("condition_id", market.get("conditionId", ""))

            if not condition_id:
                print(f"#{i}: No condition_id in market | {q}")
                unable_to_verify.append((i, t))
                time.sleep(0.5)
                continue

            # Now query CLOB for resolution
            clob_resp = requests.get(
                f"https://clob.polymarket.com/markets/{condition_id}",
                timeout=10
            )
            if clob_resp.status_code != 200:
                print(f"#{i}: CLOB API error {clob_resp.status_code} | {q}")
                unable_to_verify.append((i, t))
                time.sleep(0.5)
                continue

            clob_data = clob_resp.json()
            tokens = clob_data.get("tokens", [])

            # Find winner
            clob_resolution = None
            for tok in tokens:
                if tok.get("winner") is True:
                    tok_outcome = (tok.get("outcome") or "").upper()
                    if tok_outcome in ("UP", "YES"):
                        clob_resolution = "YES"
                    elif tok_outcome in ("DOWN", "NO"):
                        clob_resolution = "NO"
                    break

            if clob_resolution is None:
                print(f"#{i}: CLOB not yet resolved | {q}")
                unable_to_verify.append((i, t))
                time.sleep(0.5)
                continue

            # Compare with Binance result
            # If outcome matches clob_resolution → WIN, else → LOSS
            clob_won = (outcome == clob_resolution) or \
                       (outcome == "YES" and clob_resolution == "YES") or \
                       (outcome == "NO" and clob_resolution == "NO")
            clob_result = "WIN" if clob_won else "LOSS"

            if clob_result == result:
                print(f"#{i}: ✅ CORRECT | {result} | CLOB={clob_resolution} | {q}")
                verified_correct.append((i, t))
            else:
                # MISMATCH!
                shares = amount / entry if entry > 0 else 0
                if clob_won:
                    correct_pnl = round(shares * 1.0 - amount, 2)
                else:
                    correct_pnl = round(0 - amount, 2)

                print(f"#{i}: ❌ WRONG! Binance={result}, CLOB={clob_result} | "
                      f"PnL: ${pnl:.2f} → ${correct_pnl:.2f} | {q}")
                corrections_needed.append((i, t, clob_result, correct_pnl))

            time.sleep(0.3)  # Rate limit

        except Exception as e:
            print(f"#{i}: ERROR: {e} | {q}")
            unable_to_verify.append((i, t))
            time.sleep(0.5)

    # Summary
    print(f"\n{'='*60}")
    print(f"VERIFICATION SUMMARY")
    print(f"{'='*60}")
    print(f"Verified correct: {len(verified_correct)}")
    print(f"WRONG (need correction): {len(corrections_needed)}")
    print(f"Unable to verify: {len(unable_to_verify)}")

    if corrections_needed:
        print(f"\n{'='*60}")
        print(f"CORRECTIONS NEEDED")
        print(f"{'='*60}")
        total_capital_delta = 0
        for i, t, correct_result, correct_pnl in corrections_needed:
            old_pnl = t.get("pnl", 0)
            delta = correct_pnl - old_pnl
            total_capital_delta += delta
            print(f"  #{i}: {t.get('result')} → {correct_result} | "
                  f"PnL ${old_pnl:.2f} → ${correct_pnl:.2f} | "
                  f"Capital delta: ${delta:.2f}")

        print(f"\n  Total capital adjustment needed: ${total_capital_delta:.2f}")
        print(f"  Current capital: ${data.get('capital', 0):.4f}")
        print(f"  Corrected capital: ${data.get('capital', 0) + total_capital_delta:.4f}")

        # Ask user to confirm before applying
        confirm = input("\nApply corrections? (yes/no): ").strip().lower()
        if confirm == "yes":
            apply_corrections(data, corrections_needed)
        else:
            print("Corrections NOT applied. Run again to apply.")
    else:
        print("\nNo corrections needed — all remaining Binance trades verified correct!")


def apply_corrections(data, corrections):
    """Apply corrections to positions.json"""
    total_pnl_delta = 0

    for i, t, correct_result, correct_pnl in corrections:
        trade = data["closed"][i]
        old_pnl = trade["pnl"]
        amount = trade["amount"]
        entry = trade["entry_price"]
        shares = amount / entry if entry > 0 else 0

        if correct_result == "WIN":
            trade["close_price"] = 1.0
            trade["payout"] = round(shares * 1.0, 2)
        else:
            trade["close_price"] = 0.0
            trade["payout"] = 0.0

        trade["pnl"] = correct_pnl
        trade["result"] = correct_result
        trade["resolution_corrected"] = True
        trade["correction_note"] = f"Binance resolution wrong: was {t.get('result')}, CLOB says {correct_result}"

        pnl_delta = correct_pnl - old_pnl
        total_pnl_delta += pnl_delta

    data["capital"] = round(data["capital"] + total_pnl_delta, 4)
    data["daily"]["pnl"] = round(data["daily"]["pnl"] + total_pnl_delta, 2)

    with open(POSITIONS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

    print(f"\nApplied {len(corrections)} corrections.")
    print(f"Capital adjusted by ${total_pnl_delta:.2f} → ${data['capital']:.4f}")


if __name__ == "__main__":
    main()
