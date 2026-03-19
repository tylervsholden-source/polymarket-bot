import asyncio
from core.polymarket_client import PolymarketClient
from agents.orchestrator import Orchestrator
from strategies.arbitrage_engine import ArbitrageEngine

async def test():
    client = PolymarketClient()
    markets = await client.get_active_markets()

    orc = Orchestrator()
    candidates = orc._pre_filter(markets)
    print(f"Pre-filter sonrasi: {len(candidates)} market")

    engine = ArbitrageEngine(client.session)
    signals = await engine.analyze(candidates[:30], capital=100.0)

    print(f"Sinyaller: {len(signals)}")
    for s in signals[:5]:
        print(f"  [{s.signal_type}] {s.market['question'][:55]}")
        print(f"    B={s.bayesian_prob:.3f} | P={s.market_price:.3f} | Edge={s.edge:.3f} | ${s.size:.2f} | Z={s.z_score:.1f}")

    await client.session.aclose()

asyncio.run(test())
