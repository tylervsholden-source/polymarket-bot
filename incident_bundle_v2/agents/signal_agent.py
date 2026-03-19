from __future__ import annotations

import asyncio
import json
import os
import re

try:
    import anthropic as _anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _anthropic = None
    _HAS_ANTHROPIC = False

from loguru import logger

from agents.context_fetcher import fetch_context
from agents.hit_rate_tracker import HitRateTracker
from agents.market_classifier import classify as classify_market
from agents.market_index_watcher import market_watcher

_hit_tracker = HitRateTracker()


class SignalAgent:
    def __init__(self):
        if not _HAS_ANTHROPIC:
            logger.warning("anthropic paketi kurulu degil. SignalAgent devre disi.")
            self.client = None
        else:
            self.client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = "claude-opus-4-6"

    async def analyze(
        self,
        market: dict,
        whale_data: dict,
        moonshot: bool = False,
    ) -> dict | None:
        """
        1. Market tipini sınıflandır
        2. Harici context çek (hava/spor/genel)
        3. Geçmiş hit-rate istatistiğini al
        4. Hepsini Claude'a ver → probability tahmini

        moonshot=True ise düşük olasılıklı marketlerde
        "sıfırdan büyük şans var mı?" sorusunu sorar.
        """
        # Önce kategoriyi senkron olarak belirle
        classification = classify_market(market.get("question", ""))
        category = classification["category"]
        whale_dir = whale_data.get("direction", "NEUTRAL")

        # Harici context + hit-rate paralel çek
        ctx, hit_ctx = await asyncio.gather(
            fetch_context(market),
            asyncio.to_thread(_hit_tracker.get_context, category, whale_dir),
        )

        # Global piyasa endeksi verisi (ilgili endeksler varsa)
        index_ctx = market_watcher.get_context(market.get("question", ""))

        prompt = (
            self._build_moonshot_prompt(market, whale_data, ctx)
            if moonshot
            else self._build_prompt(market, whale_data, ctx, hit_ctx, index_ctx)
        )

        if self.client is None:
            logger.warning("SignalAgent: anthropic client yok, None donuluyor.")
            return None

        try:
            response = await asyncio.to_thread(
                self.client.messages.create,
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
            result = self._parse_response(text)
            if result:
                result["category"] = ctx.get("category", "GENERAL")
            return result
        except Exception as e:
            logger.error(f"Signal agent hatası: {e}")
            return None

    def _build_prompt(
        self,
        market: dict,
        whale_data: dict,
        ctx: dict,
        hit_ctx: dict,
        index_ctx: str | None = None,
    ) -> str:
        category = ctx.get("category", "GENERAL")
        subcategory = ctx.get("subcategory", "")
        external_summary = ctx.get("summary", "")
        hit_summary = hit_ctx.get("summary", "")

        # Kategori bazlı ek talimat
        category_hint = {
            "CRYPTO": (
                "Bu bir KRIPTO marketi. BİRİNCİL SİNYAL: BTC ve büyük kripto endekslerinin "
                "şu anki yönü ve momentum'u. Global piyasa endeksleriyle (NASDAQ, S&P500) "
                "korelasyonu değerlendir — risk-on/risk-off modu baskın belirleyicidir. "
                "Whale hareketleri ikincil sinyal."
            ),
            "FINANCE": (
                "Bu bir FİNANS/EKONOMİ marketi. NASDAQ, S&P500, BTC korelasyonuna bak. "
                "Güncel endeks hareketi (yukarı/aşağı momentum) tahmin için birincil veri. "
                "Fed kararları ve makro veriler ikincil destek."
            ),
            "POLITICS": (
                "Bu bir SİYASET marketi. Son anket verileri ve olayların piyasa fiyatına "
                "yansımasını değerlendir. Ekonomik göstergelerle (dolar, borsa) ilişkisine bak."
            ),
            "GEOPOLITICS": (
                "Bu bir JEOPOLİTİK marketi. Haberlerdeki son gelişmelere ve piyasaların "
                "(dolar, altın, petrol, S&P500) tepkisine göre değerlendir."
            ),
            "SPORTS": (
                "Bu bir FUTBOL marketi. Kısa vadeli — son form, saha avantajı ve "
                "son dakika kadro haberlerine odaklan."
            ),
        }.get(category, "")

        return f"""Sen bir prediction market analisti olarak çalışıyorsun.
Aşağıdaki tüm verileri değerlendirerek YES sonucunun gerçekleşme olasılığını tahmin et.

═══════════════════════════════════════
MARKET BİLGİSİ
═══════════════════════════════════════
SORU     : {market.get('question', 'N/A')}
KATEGORİ : {category} / {subcategory}
KAPANIŞ  : {market.get('end_date_iso', 'N/A')}
YES FİYAT: {market.get('best_ask', 'N/A')}
HACİM    : ${float(market.get('volume', 0) or 0):,.0f}

═══════════════════════════════════════
WHALE AKTİVİTESİ (Son 2 saat)
═══════════════════════════════════════
Büyük alım   : {whale_data.get('large_buys', 0)}
Büyük satış  : {whale_data.get('large_sells', 0)}
Net yön      : {whale_data.get('direction', 'NEUTRAL')}
Toplam hacim : ${whale_data.get('total_volume', 0):,.0f}

═══════════════════════════════════════
HARİCİ CONTEXT VERİSİ
═══════════════════════════════════════
{external_summary if external_summary else "Bu kategori için harici veri yok."}

═══════════════════════════════════════
GEÇMİŞ İSABET İSTATİSTİĞİ
═══════════════════════════════════════
{hit_summary}
{f"""
═══════════════════════════════════════
GLOBAL PİYASA ENDEKSLERİ (Güncel)
═══════════════════════════════════════
{index_ctx}
""" if index_ctx else ""}
═══════════════════════════════════════
ANALİZ TALİMATI
═══════════════════════════════════════
{category_hint}
1. Harici context verisini (hava/spor/genel) birincil kaynak olarak kullan
2. Whale yönünü destekleyici sinyal olarak değerlendir
3. Geçmiş isabet istatistiğini güven ayarlamasında kullan
4. Piyasa fiyatı ile kendi tahminini karşılaştır — sadece gerçek edge varsa BUY/SELL yönlendir

Yanıtını SADECE şu JSON formatında ver, başka hiçbir şey yazma (reasoning max 80 karakter):
{{"probability": 0.XX, "confidence": "HIGH/MEDIUM/LOW", "reasoning": "kısa açıklama"}}"""

    def _build_moonshot_prompt(self, market: dict, whale_data: dict, ctx: dict) -> str:
        """
        Moonshot modu için özel prompt.
        Ana soru: 'Bu olayın gerçekleşme ihtimali sıfırdan büyük mü?'
        Yüksek kesinlik değil, non-zero şans yeterli.
        """
        external_summary = ctx.get("summary", "")
        price = float(market.get("best_ask", 0.03) or 0.03)
        payout = int((1.0 / price) - 1.0) if price > 0 else 0

        return f"""Sen bir prediction market analistiyorsun ve UZAK OLASILI (long-shot) fırsatları değerlendiriyorsun.

Bu market şu anda çok düşük fiyatla işlem görüyor: {price:.3f} ({payout:.0f}x payout)
Piyasa bu olayın gerçekleşmesini neredeyse imkânsız buluyor.

SORU     : {market.get('question', 'N/A')}
KAPANIŞ  : {market.get('end_date_iso', 'N/A')}
YES FİYAT: {price}
HACİM    : ${float(market.get('volume', 0) or 0):,.0f}
WHALE YÖN: {whale_data.get('direction', 'NEUTRAL')} (alım:{whale_data.get('large_buys', 0)} satış:{whale_data.get('large_sells', 0)})

HARICI VERİ:
{external_summary if external_summary else "Mevcut değil."}

MOONSHOT ANALİZİ:
Bu olayın gerçekleşme ihtimali gerçekten SIFIR mi, yoksa piyasanın göz ardı ettiği bir senaryo var mı?
- Süpriz gelişme, kaza, hava koşulu, yanlış fiyatlama olabilir mi?
- Whale alımı varsa: akıllı para mı yoksa noise mi?
- Piyasanın bu fiyatı vermesinin sebebi gerçekten imkânsız olduğu için mi, yoksa kimse bakmadığı için mi?

KURAL: Eğer gerçekleşme olasılığı {price:.3f}'dan (piyasa fiyatı) anlamlı ölçüde yüksekse probability ver.
Eğer gerçekten sıfıra yakınsa 0.01 ver.

Yanıtını SADECE şu JSON formatında ver (reasoning max 80 karakter):
{{"probability": 0.XX, "confidence": "HIGH/MEDIUM/LOW", "reasoning": "kısa açıklama"}}"""

    def _parse_response(self, text: str) -> dict | None:
        try:
            match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
            if not match:
                raise ValueError("JSON bloğu bulunamadı")
            data = json.loads(match.group())
            prob = float(data["probability"])
            if not 0 < prob < 1:
                logger.warning(f"Geçersiz olasılık: {prob}")
                return None
            return data
        except Exception as e:
            preview = text.replace("\n", " ")
            logger.warning(f"AI yanıtı parse edilemedi: {e} | Yanıt: {preview}")
            return None
