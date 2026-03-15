import os
import json
from loguru import logger

try:
    import anthropic as _anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _anthropic = None
    _HAS_ANTHROPIC = False


class MrTortureAgent:
    def __init__(self):
        if not _HAS_ANTHROPIC:
            logger.warning("anthropic paketi yok -- MrTortureAgent devre disi.")
            self.client = None
            return
        self.client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = "claude-opus-4-6"  # Required proxy model string
        self.prompt = (
            "You are Mr. Torture. You are a ruthless, highly analytical, and perfectionist AI Meta-Agent. "
            "Your job is to scrutinize the analysis, decisions, and consensus of other AI hedge fund agents. "
            "You must find their logical fallacies, inaccuracies, blind spots, and bottleneck points. "
            "You aggressively criticize their naive assumptions. You demand absolute perfection. "
            "If their analysis lacks depth or takes unnecessary risks, you tear it apart. "
            "Finally, you provide actionable improvements and your own OVERRIDE DECISION. "
            "CRITICAL RULE: Losing is forbidden. You only take win-win bets. Prioritize absolute capital preservation above all else."
        )

    async def evaluate(self, asset_data: dict, agents_results: dict) -> dict | None:
        """
        Receives the raw asset data and the collective results from the other hedge fund agents.
        Returns a ruthless critique and a final optimized decision.
        """
        if self.client is None:
            logger.debug("MrTortureAgent: anthropic yok, atlanıyor.")
            return None
        prompt = self._build_prompt(asset_data, agents_results)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1500,
                system=self.prompt,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text
            return self._parse_response(text)
        except Exception as e:
            import traceback
            print(f"CRITICAL MR. TORTURE ERROR: {repr(e)}")
            traceback.print_exc()
            logger.error(f"Mr. Torture agent hatası: {e}")
            return None

    def _build_prompt(self, asset_data: dict, agents_results: dict) -> str:
        return f"""Lütfen aşağıdaki varlık verilerini ve diğer yatırım ajanlarının analizlerini ACIKÇA (Ruthlessly) değerlendir:

VARLIK/DURUM: {asset_data.get('name', 'N/A')}
BİLGİLER: {json.dumps(asset_data.get('details', {}), indent=2, ensure_ascii=False)}

AJANLARIN SONUÇLARI:
{json.dumps(agents_results, indent=2, ensure_ascii=False)}

Görevlerin:
1. Inaccuracies (Yanlışlıklar/Yanılgılar): Ajanların analizlerindeki mantıksal hataları, aşırı iyimserliği veya göremedikleri kör noktaları tespit et.
2. Bottlenecks (Dar Boğazlar): Bu yatırımın veya ajanların mantığındaki en zayıf halkaları ve tıkanıklık noktalarını belirle.
3. Improvements (İyileştirmeler): Karar alma sürecini veya bu yatırımı nasıl 'win-win' seviyesine çıkarabileceğimizi açıkla.
4. Final Decision (Nihai Karar): Sadece kusursuz ve kaybetme riski %0'a yakın (absolutely win-win) ise BUY de, yoksa acımasızca SELL veya HOLD ver.

Yanıtını SADECE şu JSON formatında ver:
{{
    "inaccuracies": "Ajanların hataları ve kör noktaları",
    "bottlenecks": "Yatırımdaki veya mantıktaki en zayıf halkalar",
    "improvements": "Yapılması gereken iyileştirmeler ve strateji optimizasyonu",
    "decision": "BUY/SELL/HOLD",
    "reasoning": "Nihai acımasız kararın özeti"
}}"""

    def _parse_response(self, text: str) -> dict | None:
        import re
        # Debugging için dump et
        try:
            with open("dump.txt", "w", encoding="utf-8") as f:
                f.write(text)
        except:
            pass

        try:
            # Önce en geniş parantez bloğunu bulmaya çalış (greedy)
            match = re.search(r"(\{.*\})", text, re.DOTALL)
            if not match:
                 # Kod bloklarını dene
                 match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            
            if not match:
                raise ValueError("JSON formatında blok bulunamadı.")
                
            cleaned_json = match.group(1) if match.groups() else match.group(0)
            data = json.loads(cleaned_json)
            return data
        except Exception as e:
            # Eğer hala hata varsa, daha spesifik arama yap
            try:
                # Sadece ilk { ve son } arası
                start = text.find('{')
                end = text.rfind('}')
                if start != -1 and end != -1:
                    data = json.loads(text[start:end+1])
                    return data
            except:
                pass
            logger.warning(f"Mr. Torture yanıtı parse edilemedi: {e} | Yanıt: {text[:200]}")
            return None
