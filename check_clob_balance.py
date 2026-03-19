"""CLOB py-clob-client ile bakiye kontrol."""
import os
from dotenv import load_dotenv
load_dotenv()

from core.polymarket_client import PolymarketClient

client = PolymarketClient()
clob = client._clob

if not clob:
    print("CLOB baslatilamadi!")
else:
    print("CLOB baslatildi:", clob)
    print()

    # Tum metodlari listele (balance ile ilgili)
    all_methods = [m for m in dir(clob) if not m.startswith("_")]
    print("Tum metodlar:")
    for m in all_methods:
        print(f"  {m}")

    # get_balance_allowance dogru parametreyle
    try:
        from py_clob_client.clob_types import AssetType
        print("\nAssetType:", list(AssetType))
    except Exception as e:
        print(f"AssetType import: {e}")

    try:
        result = clob.get_balance_allowance()
        print(f"\nBalance allowance: {result}")
    except Exception as e:
        print(f"\nget_balance_allowance(): {e}")

    # get_trades
    try:
        trades = clob.get_trades(params={"maker_address": os.getenv("POLYMARKET_WALLET_ADDRESS"), "limit": 5})
        print(f"\nSon trades: {trades}")
    except Exception as e:
        print(f"get_trades: {e}")
