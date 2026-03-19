"""
Polymarket API Credentials Generator

Private key'den API credentials türetir.
Çalıştır: python generate_api_creds.py
"""
import os
from dotenv import load_dotenv

load_dotenv()

private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
wallet_address = os.getenv("POLYMARKET_WALLET_ADDRESS")

if not private_key or not wallet_address:
    print("Hata: .env dosyasına şunları ekle:")
    print("  POLYMARKET_PRIVATE_KEY=0x...")
    print("  POLYMARKET_WALLET_ADDRESS=0x...")
    exit(1)

try:
    from py_clob_client.client import ClobClient

    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key,
        chain_id=137,
        signature_type=1,  # POLY_PROXY (Gmail login)
        funder=wallet_address,
    )

    creds = client.create_or_derive_api_creds()
    print("\nCredentials turetildi:")
    print(f"  POLYMARKET_API_KEY={creds.api_key}")
    print(f"  POLYMARKET_SECRET={creds.api_secret}")
    print(f"  POLYMARKET_PASSPHRASE={creds.api_passphrase}")
    print("\nBunları .env dosyasına kopyala (isteğe bağlı — bot zaten otomatik türetiyor).")

except ImportError:
    print("py-clob-client kurulu değil: pip install py-clob-client")
except Exception as e:
    print(f"Hata: {e}")
