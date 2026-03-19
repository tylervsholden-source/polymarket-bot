"""
Tüm açık pozisyonları kapat — Polymarket CLOB SELL emri.
"""
import os, requests
from dotenv import load_dotenv
load_dotenv()

CLOB_HOST = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CHAIN_ID = 137
WALLET = os.getenv("POLYMARKET_WALLET_ADDRESS", "")

def init_clob():
    from py_clob_client.client import ClobClient
    clob = ClobClient(
        host=CLOB_HOST,
        key=os.getenv("POLYMARKET_PRIVATE_KEY"),
        chain_id=CHAIN_ID,
        signature_type=1,
        funder=WALLET,
    )
    clob.set_api_creds(clob.create_or_derive_api_creds())
    return clob

def get_positions():
    resp = requests.get(f"{DATA_API}/positions", params={"user": WALLET}, timeout=15)
    resp.raise_for_status()
    return resp.json()

def close_all():
    print(f"Wallet: {WALLET}")
    print("Pozisyonlar çekiliyor...")

    all_pos = get_positions()
    # Sadece size > 0.01 olanlar
    active = [p for p in all_pos if float(p.get("size", 0) or 0) > 0.01]

    if not active:
        print("Kapatılacak pozisyon yok.")
        return

    print(f"\n{len(active)} pozisyon bulundu:\n")
    total_val = 0
    for i, p in enumerate(active, 1):
        size = float(p["size"])
        cur_val = float(p.get("currentValue", 0) or 0)
        total_val += cur_val
        print(f"  {i}. conditionId: {p['conditionId'][:20]}...")
        print(f"     asset: {p['asset'][:20]}...")
        print(f"     Size: {size:.2f} | Değer: ${cur_val:.2f}")
    print(f"\nToplam değer: ${total_val:.2f}\n")

    print("CLOB client başlatılıyor...")
    clob = init_clob()

    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import SELL

    success, failed = 0, 0
    for p in active:
        token_id = p["asset"]  # token ID
        size = round(float(p["size"]), 2)

        # Order book'tan fiyat al
        price = 0.01
        try:
            book = clob.get_order_book(token_id)
            bids = book.get("bids", [])
            if bids:
                price = float(bids[0]["price"])
        except Exception:
            pass

        price = max(round(price, 4), 0.01)
        print(f"SELL token={token_id[:16]}... size={size} @ {price}", end=" ")

        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=SELL,
            )
            signed = clob.create_order(order_args)
            resp = clob.post_order(signed, OrderType.GTC)
            order_id = resp.get("orderID") or resp.get("id", str(resp))
            print(f"-> OK: {order_id}")
            success += 1
        except Exception as e:
            print(f"-> HATA: {e}")
            failed += 1

    print(f"\nTamamlandı: {success} başarılı, {failed} başarısız.")

if __name__ == "__main__":
    close_all()
