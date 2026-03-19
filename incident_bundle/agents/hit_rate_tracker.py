"""
Hit-Rate Tracker — Geçmiş kapalı marketlerin isabet örüntülerini analiz eder.

Hangi sinyallerin (whale yönü, market tipi, güven seviyesi) gerçekten doğru
kapandığını takip eder. Bu bilgiyi SignalAgent'a context olarak verir.

Veri: data/hit_rates.json dosyasında saklanır.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

DATA_DIR = Path("data")
HIT_RATE_FILE = DATA_DIR / "hit_rates.json"

# Minimum kayıt sayısı — bu kadardan az geçmiş varsa "yetersiz veri"
MIN_SAMPLES = 5


class HitRateTracker:
    """
    Kapanan her pozisyon için isabet kaydı tutar.
    İsabet = biz BUY dedik, YES kapandı  |  biz SELL dedik, NO kapandı

    Gruplandırma boyutları:
      - market_category   (SPORTS, WEATHER, POLITICS, ...)
      - whale_direction   (BUY, SELL, NEUTRAL)
      - combined          (category + whale_direction)
    """

    def __init__(self):
        DATA_DIR.mkdir(exist_ok=True)
        self._data: dict[str, Any] = self._load()

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def record(
        self,
        market_id: str,
        category: str,
        whale_direction: str,
        our_decision: str,   # "BUY" | "SELL"
        resolved_yes: bool,  # market YES ile mi kapandı?
    ) -> None:
        """Kapanan bir pozisyonu kaydet."""
        hit = (our_decision == "BUY" and resolved_yes) or \
              (our_decision == "SELL" and not resolved_yes)

        entry = {
            "market_id": market_id,
            "category": category,
            "whale_direction": whale_direction,
            "our_decision": our_decision,
            "resolved_yes": resolved_yes,
            "hit": hit,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._data["records"].append(entry)
        self._rebuild_stats()
        self._save()
        logger.debug(
            f"HitRate kayıt: {category}/{whale_direction} "
            f"→ {'HIT' if hit else 'MISS'}"
        )

    def get_context(self, category: str, whale_direction: str) -> dict:
        """
        Verilen kategori + whale yönü için geçmiş isabet istatistiğini döner.

        Dönüş:
        {
            "category_hit_rate": 0.62,   # sadece kategoriye göre
            "whale_hit_rate": 0.71,      # sadece whale yönüne göre
            "combined_hit_rate": 0.68,   # kategori + whale birlikte
            "combined_samples": 14,
            "summary": "..."
        }
        """
        stats = self._data.get("stats", {})

        cat_stats = stats.get("by_category", {}).get(category, {})
        whale_stats = stats.get("by_whale", {}).get(whale_direction, {})
        combo_key = f"{category}|{whale_direction}"
        combo_stats = stats.get("by_combined", {}).get(combo_key, {})

        def hit_rate(s: dict) -> float | None:
            n = s.get("total", 0)
            if n < MIN_SAMPLES:
                return None
            return round(s.get("hits", 0) / n, 3)

        cat_hr = hit_rate(cat_stats)
        whale_hr = hit_rate(whale_stats)
        combo_hr = hit_rate(combo_stats)
        combo_n = combo_stats.get("total", 0)

        lines = []
        if cat_hr is not None:
            lines.append(f"{category} tipi marketlerde geçmiş isabet: %{cat_hr*100:.0f} ({cat_stats['total']} trade)")
        if whale_hr is not None:
            lines.append(f"Whale {whale_direction} yönünde geçmiş isabet: %{whale_hr*100:.0f} ({whale_stats['total']} trade)")
        if combo_hr is not None:
            lines.append(f"{category}+{whale_direction} kombinasyonu: %{combo_hr*100:.0f} isabet ({combo_n} trade)")

        if not lines:
            lines.append("Henüz yeterli geçmiş veri yok (min 5 trade gerekli).")

        return {
            "category_hit_rate": cat_hr,
            "whale_hit_rate": whale_hr,
            "combined_hit_rate": combo_hr,
            "combined_samples": combo_n,
            "summary": "\n".join(lines),
        }

    def get_best_patterns(self, top_n: int = 5) -> list[dict]:
        """En yüksek isabet oranına sahip kombinasyonları döner."""
        by_combined = self._data.get("stats", {}).get("by_combined", {})
        ranked = []
        for key, s in by_combined.items():
            if s.get("total", 0) >= MIN_SAMPLES:
                hr = s["hits"] / s["total"]
                cat, whale = key.split("|")
                ranked.append({
                    "category": cat,
                    "whale_direction": whale,
                    "hit_rate": round(hr, 3),
                    "samples": s["total"],
                })
        ranked.sort(key=lambda x: x["hit_rate"], reverse=True)
        return ranked[:top_n]

    def summary_stats(self) -> dict:
        """Genel istatistik özeti."""
        records = self._data.get("records", [])
        if not records:
            return {"total": 0, "hits": 0, "hit_rate": None}
        hits = sum(1 for r in records if r.get("hit"))
        return {
            "total": len(records),
            "hits": hits,
            "hit_rate": round(hits / len(records), 3),
        }

    # ──────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────

    def _rebuild_stats(self) -> None:
        by_cat: dict[str, dict] = defaultdict(lambda: {"hits": 0, "total": 0})
        by_whale: dict[str, dict] = defaultdict(lambda: {"hits": 0, "total": 0})
        by_combined: dict[str, dict] = defaultdict(lambda: {"hits": 0, "total": 0})

        for r in self._data.get("records", []):
            cat = r.get("category", "GENERAL")
            whale = r.get("whale_direction", "NEUTRAL")
            hit = int(r.get("hit", False))

            by_cat[cat]["hits"] += hit
            by_cat[cat]["total"] += 1

            by_whale[whale]["hits"] += hit
            by_whale[whale]["total"] += 1

            combo = f"{cat}|{whale}"
            by_combined[combo]["hits"] += hit
            by_combined[combo]["total"] += 1

        self._data["stats"] = {
            "by_category": dict(by_cat),
            "by_whale": dict(by_whale),
            "by_combined": dict(by_combined),
        }

    def _load(self) -> dict:
        if HIT_RATE_FILE.exists():
            try:
                with open(HIT_RATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"hit_rates.json okunamadı: {e}")
        return {"records": [], "stats": {}}

    def _save(self) -> None:
        try:
            with open(HIT_RATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"hit_rates.json yazılamadı: {e}")
