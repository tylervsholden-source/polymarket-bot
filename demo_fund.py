import asyncio
import os
import sys
from loguru import logger
from dotenv import load_dotenv
from agents.hedge_fund_agents import get_all_hedge_fund_agents
from agents.torture_agent import MrTortureAgent
import json

# Terminal renklerinin Windows'ta çökmemesi için encoding ayarı
sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

# Basit bir asset mock datası (Polymarket veya düz coin/hisse)
SAMPLE_ASSET = {
    "name": "Ethereum Spot ETF Onayı / Fiyatı",
    "details": {
        "current_price": "$3,500",
        "market_context": "Büyük finansal kurumlar ETF onayı bekliyor. Teknolojik gelişmeler ve ağ güncellemeleri hızla devam ediyor.",
        "risk_factors": "Düzenleyici belirsizlikler (SEC), yüksek volatilite, makroekonomik faiz oranları.",
        "fundamentals": "Güçlü arz yakımı mekanizması ve akıllı kontrat pazarındaki devasa liderlik ancak geleneksel 'nakit akışı' eksikliği."
    }
}

MOCK_WHALE_DATA = {
    "direction": "BULLISH",
    "large_buys": 12,
    "large_sells": 3,
    "total_volume": 4500000
}

async def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("Lütfen çalışmaya başlamadan önce ANTHROPIC_API_KEY ortam değişkenini tanımlayın.")
        return

    logger.info("AI Hedge Fund başlatılıyor...")
    agents = get_all_hedge_fund_agents()
    
    tasks = []
    agent_names = []
    
    # Tüm ajanlardan eşzamanlı olarak fikir almak için asenkron task'lar oluştur
    for name, agent in agents.items():
        logger.info(f"{name} ({agent.persona['title']}) analize başlıyor...")
        tasks.append(agent.analyze(SAMPLE_ASSET, MOCK_WHALE_DATA))
        agent_names.append(name)

    logger.info("Ajanların analizleri bekleniyor...")
    results = await asyncio.gather(*tasks)

    # Sonuçları ekrana yazdırıyoruz ve topluyoruz
    print("\n" + "="*50)
    print("YATIRIM KOMİTESİ KARARLARI (ETHEREUM ETF) \n" + "="*50)

    aggregated_results = {}
    for name, result in zip(agent_names, results):
        title = agents[name].persona['title']
        if result:
            decision = result.get('decision', 'N/A')
            confidence = result.get('confidence', 'N/A')
            reasoning = result.get('reasoning', 'N/A')
            aggregated_results[name] = {"decision": decision, "confidence": confidence, "reasoning": reasoning}
            
            color = "\033[92m" if decision == "BUY" else ("\033[91m" if decision == "SELL" else "\033[93m")
            reset = "\033[0m"
            
            print(f"\n{color}● {name} - {title}{reset}")
            print(f"KARAR:      {decision}")
            print(f"GÜVEN:      {confidence}")
            print(f"GEREKÇE:    {reasoning}")
        else:
            aggregated_results[name] = "Yanıt alınamadı."
            print(f"\n● {name} - {title}")
            print("YANIT ALINAMADI (API Hatası veya Parse Sorunu)")

    print("\n" + "="*60)
    print(" 🚨 MR. TORTURE ACIMASIZ DEĞERLENDİRMESİ BAŞLIYOR 🚨 ")
    print("="*60)

    torture_agent = MrTortureAgent()
    torture_result = await torture_agent.evaluate(SAMPLE_ASSET, aggregated_results)

    if torture_result:
        print("\n\033[1;35m🔥 MR. TORTURE'UN SONUÇ RAPORU 🔥\033[0m")
        print("\n\033[93m[1] INACCURACIES (Yanlışlıklar/Kör Noktalar):\033[0m")
        print(torture_result.get('inaccuracies', 'N/A'))
        
        print("\n\033[91m[2] BOTTLENECKS (Dar Boğazlar):\033[0m")
        print(torture_result.get('bottlenecks', 'N/A'))

        print("\n\033[96m[3] IMPROVEMENTS (İyileştirme ve Optimizasyon):\033[0m")
        print(torture_result.get('improvements', 'N/A'))

        decision = torture_result.get('decision', 'HOLD')
        color = "\033[1;92m" if decision == "BUY" else ("\033[1;91m" if decision == "SELL" else "\033[1;93m")
        print(f"\n{color}[4] GADDAR NİHAİ KARAR: {decision}\033[0m")
        print(f"GEREKÇE: {torture_result.get('reasoning', 'N/A')}")
    else:
        print("\nMr. Torture'dan sonuç alınamadı.")

if __name__ == "__main__":
    asyncio.run(main())
