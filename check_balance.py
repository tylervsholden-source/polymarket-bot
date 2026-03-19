import asyncio, httpx
from dotenv import load_dotenv
import os

load_dotenv()

async def main():
    session = httpx.AsyncClient(timeout=15)
    wallet = os.getenv("POLYMARKET_WALLET_ADDRESS")

    resp = await session.get("https://data-api.polymarket.com/value", params={"user": wallet})
    data = resp.json()
    total_value = float(data[0]["value"]) if isinstance(data, list) and data else 0
    print(f"Toplam portfoy: ${total_value:.4f}")

    resp2 = await session.get("https://data-api.polymarket.com/positions", params={"user": wallet, "limit": 50})
    positions = resp2.json() if isinstance(resp2.json(), list) else []
    pos_value = sum(float(p.get("currentValue", 0) or 0) for p in positions)
    print(f"Acik pozisyon degeri: ${pos_value:.4f}")
    print(f"Tahmini USDC cash: ${total_value - pos_value:.4f}")
    print()
    print("Acik pozisyonlar:")
    for p in positions:
        cv = float(p.get("currentValue", 0) or 0)
        print(f"  {p['title'][:50]} | {p['outcome']} curPrice={p['curPrice']} val=${cv:.4f}")

    await session.aclose()

asyncio.run(main())
