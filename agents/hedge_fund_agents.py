from __future__ import annotations

import asyncio
import json
import os
import re
from loguru import logger
import anthropic


PERSONAS: dict[str, dict] = {
    "Aswath Damodaran": {
        "title": "The Dean of Valuation",
        "description": "Focuses on story, numbers, and disciplined valuation.",
        "prompt": (
            "You are Aswath Damodaran, the Dean of Valuation. Your investment philosophy "
            "is heavily rooted in intrinsic valuation, combining numbers (cash flow, risk, growth) "
            "with an underlying narrative. You always demand disciplined valuation and never overpay. "
            "CRITICAL RULE: Losing is forbidden. You only take win-win bets. Prioritize absolute capital preservation above all else."
        ),
    },
    "Ben Graham": {
        "title": "The Godfather of Value Investing",
        "description": "Only buys hidden gems with a massive margin of safety.",
        "prompt": (
            "You are Ben Graham, the godfather of value investing. You strictly look for a "
            "massive 'margin of safety' and hidden gems trading below their intrinsic value. "
            "You ignore market noise. CRITICAL RULE: Losing is forbidden. You only take win-win bets. Prioritize absolute capital preservation above all else."
        ),
    },
    "Bill Ackman": {
        "title": "The Activist Investor",
        "description": "Takes bold concentrated positions and pushes for change with capped downside.",
        "prompt": (
            "You are Bill Ackman. You take large, highly concentrated, bold positions. "
            "You look for good underlying situations that need a catalyst or change. "
            "CRITICAL RULE: Losing is forbidden. You only take win-win bets. Prioritize absolute capital preservation above all else."
        ),
    },
    "Charlie Munger": {
        "title": "The Mental Models Master",
        "description": "Only buys wonderful businesses at fair prices with zero risk of ruin.",
        "prompt": (
            "You are Charlie Munger. You look for 'wonderful businesses at fair prices' "
            "using a lattice-work of mental models. You rely on first-principles thinking. "
            "CRITICAL RULE: Losing is forbidden. You only take win-win bets. Prioritize absolute capital preservation above all else."
        ),
    },
    "Michael Burry": {
        "title": "The Big Short Contrarian",
        "description": "Hunts for deep value, contrarian plays with asymmetric upside vs zero downside.",
        "prompt": (
            "You are Michael Burry. You are an extreme contrarian who hunts for deep value "
            "and bets against popular consensus. You dive deep into structural flaws. "
            "CRITICAL RULE: Losing is forbidden. You only take win-win bets. Prioritize absolute capital preservation above all else."
        ),
    },
    "Mohnish Pabrai": {
        "title": "The Dhandho Investor",
        "description": "Looks for 'Heads I win, tails I don't lose much' setups.",
        "prompt": (
            "You are Mohnish Pabrai, the Dhandho investor. Your motto is "
            "'Heads I win, tails I don't lose much.' You look for highly asymmetric setups. "
            "CRITICAL RULE: Losing is forbidden. You only take win-win bets. Prioritize absolute capital preservation above all else."
        ),
    },
    "Peter Lynch": {
        "title": "The Practical Ten-Bagger Hunter",
        "description": "Seeks ten-baggers in what he knows with solid safety nets.",
        "prompt": (
            "You are Peter Lynch. You believe in investing in what you know. "
            "You look for 'ten-baggers' by observing everyday trends and finding easy-to-understand situations. "
            "CRITICAL RULE: Losing is forbidden. You only take win-win bets. Prioritize absolute capital preservation above all else."
        ),
    },
}


class HedgeFundPersonaAgent:
    def __init__(self, persona_name: str):
        if persona_name not in PERSONAS:
            raise ValueError(
                f"Persona '{persona_name}' bulunamadı. Mevcut: {list(PERSONAS.keys())}"
            )
        self.persona = PERSONAS[persona_name]
        self.name = persona_name
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = "claude-opus-4-6"  # Required proxy model string

    async def analyze(self, market: dict, whale_data: dict) -> dict | None:
        prompt = self._build_prompt(market, whale_data)
        try:
            # Anthropic SDK senkron — thread pool'da çalıştır
            response = await asyncio.to_thread(
                self.client.messages.create,
                model=self.model,
                max_tokens=512,
                system=self.persona["prompt"],
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
            return self._parse_response(text)
        except Exception as e:
            logger.error(f"{self.name} agent hatası: {e}")
            return None

    def _build_prompt(self, market: dict, whale_data: dict) -> str:
        return f"""Aşağıdaki prediction market sorusunu kendi yatırım felsefenle değerlendir.

SORU: {market.get('question', 'N/A')}
KAPANIŞ: {market.get('end_date_iso', 'N/A')}
MEVCUT YES FİYATI: {market.get('best_ask', 'N/A')}
HACİM: ${float(market.get('volume', 0) or 0):,.0f}

WHALE AKTİVİTESİ: {whale_data.get('direction', 'NEUTRAL')} \
(alım:{whale_data.get('large_buys', 0)} satış:{whale_data.get('large_sells', 0)})

Yatırım felsefeni bu prediction market'e uygula:
- BUY: YES outcome gerçekleşecek, piyasa fiyatı düşük
- SELL: YES outcome gerçekleşmeyecek veya fiyat yüksek
- HOLD: Yetersiz veri veya nötr görüş

Yanıtını SADECE şu JSON formatında ver:
{{"decision": "BUY/SELL/HOLD", "confidence": "HIGH/MEDIUM/LOW", "reasoning": "kısa açıklama"}}"""

    def _parse_response(self, text: str) -> dict | None:
        try:
            match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
            if not match:
                raise ValueError("JSON bulunamadı")
            data = json.loads(match.group())
            if data.get("decision") not in ("BUY", "SELL", "HOLD"):
                return None
            return data
        except Exception as e:
            logger.warning(f"{self.name} parse hatası: {e}")
            return None


class HedgeFundConsensusAgent:
    """
    Tüm (veya seçili) hedge fund personalarını paralel çalıştırır.
    Oyları toplar → SignalAgent ile aynı formatta probability döner.

    BUY  → YES olasılığını artırır
    SELL → YES olasılığını düşürür
    HOLD → nötr (0.5)

    Güven ağırlıkları: HIGH=1.0, MEDIUM=0.6, LOW=0.3
    """

    CONFIDENCE_WEIGHTS = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}
    DECISION_SCORES = {"BUY": 1.0, "HOLD": 0.5, "SELL": 0.0}

    def __init__(self, personas: list[str] | None = None):
        names = personas or list(PERSONAS.keys())
        self.agents = [HedgeFundPersonaAgent(n) for n in names]

    async def analyze(self, market: dict, whale_data: dict) -> dict | None:
        """SignalAgent.analyze() ile aynı çıktı formatı: {probability, confidence, reasoning}"""
        # Tüm personaları paralel çalıştır
        results = await asyncio.gather(
            *[agent.analyze(market, whale_data) for agent in self.agents],
            return_exceptions=True,
        )

        votes: list[dict] = []
        for agent, result in zip(self.agents, results):
            if isinstance(result, dict) and result:
                votes.append({"name": agent.name, **result})
                logger.debug(
                    f"{agent.name}: {result.get('decision')} ({result.get('confidence')})"
                )

        if not votes:
            logger.warning("Hiçbir persona yanıt vermedi.")
            return None

        # Ağırlıklı ortalama
        total_weight = 0.0
        weighted_score = 0.0
        buy_count = sum(1 for v in votes if v["decision"] == "BUY")
        sell_count = sum(1 for v in votes if v["decision"] == "SELL")

        for v in votes:
            w = self.CONFIDENCE_WEIGHTS.get(v.get("confidence", "LOW"), 0.3)
            s = self.DECISION_SCORES.get(v["decision"], 0.5)
            weighted_score += w * s
            total_weight += w

        probability = weighted_score / total_weight if total_weight > 0 else 0.5

        # Genel güven seviyesi
        if total_weight / len(self.agents) >= 0.7:
            confidence = "HIGH"
        elif total_weight / len(self.agents) >= 0.4:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        summary = (
            f"{buy_count} BUY / {sell_count} SELL / "
            f"{len(votes) - buy_count - sell_count} HOLD — "
            f"prob={probability:.2f}"
        )
        logger.info(f"Konsensüs: {summary}")

        return {
            "probability": round(probability, 3),
            "confidence": confidence,
            "reasoning": summary,
            "votes": votes,
        }

def get_all_hedge_fund_agents():
    return {name: HedgeFundPersonaAgent(name) for name in PERSONAS.keys()}
