"""
TradeAnalyzer — Her trade sonrası detaylı analiz ve öğrenme motoru.

Her kapanan trade için:
1. Neden kazandı/kaybetti? (root cause analysis)
2. Hangi sinyaller doğruydu/yanlıştı?
3. Pattern tespiti (tekrarlayan kazanç/kayıp kalıpları)
4. Adaptif parametre önerileri

Bot'u ASLA durdurmaz — sadece bilgi üretir ve AutonomousEngine'e besler.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class TradeAnalysis:
    """Tek bir trade'in detaylı analizi."""
    market_id: str = ""
    question: str = ""
    direction: str = ""
    outcome: str = ""             # WIN, LOSS, PENDING
    pnl: float = 0.0
    edge_at_entry: float = 0.0
    confluence_at_entry: float = 0.0
    risk_flags_at_entry: list[str] = field(default_factory=list)
    entry_price: float = 0.0
    exit_price: float = 0.0
    hold_duration_seconds: float = 0.0

    # Analiz sonuçları
    root_cause: str = ""
    signal_accuracy: dict = field(default_factory=dict)  # {"whale": True, "regime": False, ...}
    lessons: list[str] = field(default_factory=list)
    pattern_match: str = ""       # Tespit edilen pattern adı
    confidence_was_correct: bool = False
    timestamp: float = field(default_factory=time.time)

    def summary(self) -> str:
        emoji = "✅" if self.outcome == "WIN" else "❌" if self.outcome == "LOSS" else "⏳"
        return (
            f"{emoji} {self.direction} {self.question[:50]} | "
            f"PnL=${self.pnl:+.2f} | Edge={self.edge_at_entry:.3f} | "
            f"Root: {self.root_cause}"
        )


@dataclass
class PatternStats:
    """Bir pattern'ın istatistikleri."""
    name: str
    occurrences: int = 0
    wins: int = 0
    losses: int = 0
    avg_pnl: float = 0.0
    total_pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0


class TradeAnalyzer:
    """
    Trade sonrası analiz ve pattern tanıma motoru.

    Her kapanan trade için otomatik analiz yapar,
    pattern'ları takip eder ve AutonomousEngine'e
    adaptif parametre önerileri sunar.
    """

    ANALYSIS_FILE = Path("data/trade_analyses.json")
    PATTERN_FILE = Path("data/trade_patterns.json")

    # Bilinen pattern'lar (adı → tespit koşulu)
    KNOWN_PATTERNS = {
        "BOUNCE_AFTER_STREAK": "3+ ardışık aynı yön trade sonrası ters yön kazanç",
        "HIGH_EDGE_WIN": "Edge > 0.15 olan trade kazandı",
        "LOW_EDGE_LOSS": "Edge < 0.08 olan trade kaybetti",
        "WHALE_ALIGNED_WIN": "Whale yönü ile aynı yöndeki trade kazandı",
        "WHALE_OPPOSED_LOSS": "Whale yönü ile ters trade kaybetti",
        "NO_TRAP": "NO trade'de 33% WR pattern'ı — kayıp",
        "REGIME_OVEREXTEND_LOSS": "Regime strength > 0.70 iken trade kaybetti",
        "HIGH_CONFLUENCE_WIN": "Confluence > 0.7 trade kazandı",
        "RSI_EXTREME_REVERSAL": "RSI aşırı bölgeden dönüş — ters pozisyon kazandı",
        "STALE_EDGE_LOSS": "Sinyal-execution arası fiyat değişimi nedeniyle kayıp",
    }

    def __init__(self):
        self._analyses: list[TradeAnalysis] = []
        self._patterns: dict[str, PatternStats] = {}
        self._load_history()
        logger.info("[TradeAnalyzer] Initialized — post-trade analysis active")

    # ═══════════════════════════════════════════════════════════════════
    # ANA ANALİZ FONKSİYONU
    # ═══════════════════════════════════════════════════════════════════

    def analyze_trade(
        self,
        trade: dict,
        signal_data: dict | None = None,
        all_closed: list[dict] | None = None,
    ) -> TradeAnalysis:
        """
        Kapanan bir trade'i analiz et.

        Args:
            trade: Position manager'dan kapanan trade verisi
            signal_data: Trade açılırken kaydedilen sinyal bilgileri
            all_closed: Tüm kapalı trade listesi (pattern analizi için)

        Returns:
            TradeAnalysis — detaylı analiz sonucu
        """
        analysis = TradeAnalysis(
            market_id=trade.get("market_id", trade.get("condition_id", "")),
            question=trade.get("question", ""),
            direction=trade.get("outcome", trade.get("direction", "")),
            pnl=float(trade.get("pnl", 0)),
            entry_price=float(trade.get("price", trade.get("entry_price", 0))),
            exit_price=float(trade.get("exit_price", trade.get("resolution_price", 0))),
        )

        # Win/Loss/Neutral belirle — position_manager'ın verdiği gerçek sonucu kullan
        # (dolmamış emir/iade → NEUTRAL, pnl==0 ile LOSS karıştırılmamalı)
        result = trade.get("result")
        if result in ("WIN", "LOSS", "NEUTRAL"):
            analysis.outcome = result
        else:
            analysis.outcome = "WIN" if analysis.pnl > 0 else "LOSS"

        # Signal data varsa zenginleştir
        if signal_data:
            analysis.edge_at_entry = signal_data.get("edge", 0)
            analysis.confluence_at_entry = signal_data.get("confluence_score", 0)
            analysis.risk_flags_at_entry = signal_data.get("risk_flags", [])

        # Hold süresi
        opened = trade.get("opened_at", trade.get("ts", 0))
        closed = trade.get("closed_at", time.time())
        if opened and closed:
            analysis.hold_duration_seconds = closed - opened

        # ── Root Cause Analysis ──
        analysis.root_cause = self._determine_root_cause(analysis, trade, signal_data)

        # ── Signal Accuracy ──
        analysis.signal_accuracy = self._evaluate_signal_accuracy(analysis, signal_data)

        # ── Pattern Matching ──
        analysis.pattern_match = self._match_pattern(analysis, all_closed or [])

        # ── Lessons Learned ──
        analysis.lessons = self._extract_lessons(analysis)

        # ── Pattern Stats Güncelle ──
        if analysis.pattern_match:
            self._update_pattern_stats(analysis)

        # Kaydet
        self._analyses.append(analysis)
        self._save_history()

        logger.info(f"[TradeAnalyzer] {analysis.summary()}")
        for lesson in analysis.lessons:
            logger.info(f"  📝 Lesson: {lesson}")

        return analysis

    # ═══════════════════════════════════════════════════════════════════
    # ROOT CAUSE ANALİZİ
    # ═══════════════════════════════════════════════════════════════════

    def _determine_root_cause(
        self,
        analysis: TradeAnalysis,
        trade: dict,
        signal_data: dict | None,
    ) -> str:
        """Trade'in neden kazandığını/kaybettiğini belirle."""
        if analysis.outcome == "WIN":
            # Kazanç sebepleri
            if analysis.edge_at_entry > 0.15:
                return "STRONG_EDGE: Yüksek edge doğrulandı"
            if analysis.confluence_at_entry > 0.7:
                return "HIGH_CONFLUENCE: Çoklu sinyal hizalanması doğru"
            if analysis.direction == "YES":
                return "YES_BASE_RATE: YES trade'lerin yüksek WR (%72) ile tutarlı"
            return "CORRECT_DIRECTION: Yön tahmini doğru"

        elif analysis.outcome == "NEUTRAL":
            return "UNFILLED_NEUTRAL: Emir dolmadı/iade edildi — kazanç/kayıp yok"

        else:
            # Kayıp sebepleri
            if analysis.direction == "NO" and analysis.edge_at_entry < 0.15:
                return "NO_THIN_EDGE: Yetersiz edge ile NO trade (33% WR riski)"
            if "REGIME_OVEREXTENDED" in analysis.risk_flags_at_entry:
                return "REGIME_REVERSAL: Aşırı uzamış rejimde bounce gerçekleşti"
            if "WHALE_OPPOSITION" in analysis.risk_flags_at_entry:
                return "WHALE_CONTRA: Whale yönüne karşı trade kaybetti"
            if analysis.edge_at_entry < 0.05:
                return "INSUFFICIENT_EDGE: Edge eşiğinin altında giriş"
            if len(analysis.risk_flags_at_entry) >= 3:
                return "MULTI_RISK_FLAG: 3+ risk flag'e rağmen giriş yapıldı"
            if analysis.hold_duration_seconds > 0 and analysis.hold_duration_seconds < 30:
                return "FLASH_LOSS: Çok hızlı kayıp — timing problemi"
            return "DIRECTION_ERROR: Yön tahmini yanlış"

    # ═══════════════════════════════════════════════════════════════════
    # SİNYAL DOĞRULUK ANALİZİ
    # ═══════════════════════════════════════════════════════════════════

    def _evaluate_signal_accuracy(
        self,
        analysis: TradeAnalysis,
        signal_data: dict | None,
    ) -> dict:
        """Her sinyal kaynağının doğruluğunu değerlendir."""
        accuracy = {}
        if not signal_data:
            return accuracy

        is_win = analysis.outcome == "WIN"

        # Whale yönü doğru muydu?
        whale_dir = signal_data.get("whale_direction", "")
        if whale_dir:
            whale_correct = (
                (whale_dir == "bullish" and analysis.direction == "YES" and is_win) or
                (whale_dir == "bearish" and analysis.direction == "NO" and is_win) or
                (whale_dir == "bullish" and analysis.direction == "NO" and not is_win) or
                (whale_dir == "bearish" and analysis.direction == "YES" and not is_win)
            )
            accuracy["whale"] = whale_correct

        # Regime yönü doğru muydu?
        regime_dir = signal_data.get("regime_direction", "")
        if regime_dir:
            regime_correct = (
                (regime_dir.lower() == analysis.direction.lower() and is_win) or
                (regime_dir.lower() != analysis.direction.lower() and not is_win)
            )
            accuracy["regime"] = regime_correct

        # Smart money doğru muydu?
        smart = signal_data.get("smart_money_signal", "")
        if smart:
            accuracy["smart_money"] = is_win  # Basitleştirme

        # Edge doğru muydu?
        accuracy["edge_prediction"] = is_win

        return accuracy

    # ═══════════════════════════════════════════════════════════════════
    # PATTERN EŞLEŞTİRME
    # ═══════════════════════════════════════════════════════════════════

    def _match_pattern(
        self,
        analysis: TradeAnalysis,
        all_closed: list[dict],
    ) -> str:
        """Bilinen pattern'larla eşleştir."""
        # HIGH_EDGE_WIN / LOW_EDGE_LOSS
        if analysis.outcome == "WIN" and analysis.edge_at_entry > 0.15:
            return "HIGH_EDGE_WIN"
        if analysis.outcome == "LOSS" and analysis.edge_at_entry < 0.08:
            return "LOW_EDGE_LOSS"

        # NO_TRAP
        if analysis.direction == "NO" and analysis.outcome == "LOSS":
            return "NO_TRAP"

        # REGIME_OVEREXTEND_LOSS
        if "REGIME_OVEREXTENDED" in analysis.risk_flags_at_entry and analysis.outcome == "LOSS":
            return "REGIME_OVEREXTEND_LOSS"

        # WHALE_ALIGNED_WIN / WHALE_OPPOSED_LOSS
        whale_acc = analysis.signal_accuracy.get("whale")
        if whale_acc is True and analysis.outcome == "WIN":
            return "WHALE_ALIGNED_WIN"
        if whale_acc is False and analysis.outcome == "LOSS":
            return "WHALE_OPPOSED_LOSS"

        # HIGH_CONFLUENCE_WIN
        if analysis.confluence_at_entry > 0.7 and analysis.outcome == "WIN":
            return "HIGH_CONFLUENCE_WIN"

        # BOUNCE_AFTER_STREAK
        if len(all_closed) >= 3:
            last_3 = all_closed[-3:]
            same_dir = all(t.get("outcome", t.get("direction", "")) == analysis.direction for t in last_3)
            if same_dir and analysis.outcome == "LOSS":
                return "BOUNCE_AFTER_STREAK"

        return ""

    # ═══════════════════════════════════════════════════════════════════
    # DERS ÇIKARMA
    # ═══════════════════════════════════════════════════════════════════

    def _extract_lessons(self, analysis: TradeAnalysis) -> list[str]:
        """Trade'den çıkarılacak dersleri listele."""
        lessons = []

        if analysis.outcome == "LOSS":
            if analysis.direction == "NO" and analysis.edge_at_entry < 0.15:
                lessons.append("NO trade'lerde min 0.15 edge gerekli — 33% WR riski devam ediyor")
            if len(analysis.risk_flags_at_entry) >= 3:
                lessons.append(f"3+ risk flag ({len(analysis.risk_flags_at_entry)}) → daha agresif boyut küçültme uygula")
            if "REGIME_OVEREXTENDED" in analysis.risk_flags_at_entry:
                lessons.append("Overextended regime = bounce riski — bu pattern tekrarlıyor")
            if analysis.edge_at_entry < 0.05:
                lessons.append("Edge < 0.05 trade'ler nadiren kazanıyor — filtre güçlendir")

        if analysis.outcome == "WIN":
            if analysis.edge_at_entry > 0.15:
                lessons.append("Yüksek edge (>0.15) trade'ler güvenilir — bu seviyeye öncelik ver")
            if analysis.confluence_at_entry > 0.7:
                lessons.append("Yüksek confluence kazanç ile korelasyonlu — filtre olarak kullan")

        if analysis.pattern_match:
            lessons.append(f"Pattern: {analysis.pattern_match} — {self.KNOWN_PATTERNS.get(analysis.pattern_match, '')}")

        return lessons

    # ═══════════════════════════════════════════════════════════════════
    # PATTERN İSTATİSTİKLERİ
    # ═══════════════════════════════════════════════════════════════════

    def _update_pattern_stats(self, analysis: TradeAnalysis):
        """Pattern istatistiklerini güncelle."""
        name = analysis.pattern_match
        if not name:
            return

        if name not in self._patterns:
            self._patterns[name] = PatternStats(name=name)

        stats = self._patterns[name]
        stats.occurrences += 1
        if analysis.outcome == "WIN":
            stats.wins += 1
        elif analysis.outcome == "LOSS":
            stats.losses += 1
        stats.total_pnl += analysis.pnl
        stats.avg_pnl = stats.total_pnl / stats.occurrences

    def get_pattern_report(self) -> dict[str, dict]:
        """Tüm pattern istatistiklerini döndür."""
        report = {}
        for name, stats in self._patterns.items():
            report[name] = {
                "occurrences": stats.occurrences,
                "win_rate": f"{stats.win_rate:.1%}",
                "avg_pnl": f"${stats.avg_pnl:.2f}",
                "total_pnl": f"${stats.total_pnl:.2f}",
            }
        return report

    def get_recommendations(self) -> list[str]:
        """Pattern'lara dayalı trade önerileri."""
        recs = []

        for name, stats in self._patterns.items():
            if stats.occurrences < 3:
                continue  # Yeterli veri yok

            if name == "NO_TRAP" and stats.win_rate < 0.40:
                recs.append(f"NO_TRAP pattern {stats.win_rate:.0%} WR — NO min edge artır veya kapat")

            if name == "HIGH_EDGE_WIN" and stats.win_rate > 0.70:
                recs.append(f"HIGH_EDGE_WIN {stats.win_rate:.0%} WR — yüksek edge trade'lere boyut artır")

            if name == "REGIME_OVEREXTEND_LOSS" and stats.win_rate < 0.30:
                recs.append(f"REGIME_OVEREXTEND {stats.win_rate:.0%} WR — regime>0.70 iken agresif küçült")

            if name == "WHALE_ALIGNED_WIN" and stats.win_rate > 0.65:
                recs.append(f"WHALE_ALIGNED {stats.win_rate:.0%} WR — whale hizalı trade'lere öncelik ver")

        return recs

    # ═══════════════════════════════════════════════════════════════════
    # KALICILIK
    # ═══════════════════════════════════════════════════════════════════

    def _load_history(self):
        """Geçmiş analizleri yükle."""
        try:
            if self.PATTERN_FILE.exists():
                with open(self.PATTERN_FILE) as f:
                    data = json.load(f)
                    for name, stats_dict in data.items():
                        self._patterns[name] = PatternStats(
                            name=name,
                            occurrences=stats_dict.get("occurrences", 0),
                            wins=stats_dict.get("wins", 0),
                            losses=stats_dict.get("losses", 0),
                            total_pnl=stats_dict.get("total_pnl", 0),
                            avg_pnl=stats_dict.get("avg_pnl", 0),
                        )
        except Exception as e:
            logger.debug(f"[TradeAnalyzer] Pattern load failed: {e}")

    def _save_history(self):
        """Analizleri diske kaydet."""
        try:
            self.ANALYSIS_FILE.parent.mkdir(exist_ok=True)

            # Son 200 analizi kaydet
            recent = self._analyses[-200:] if len(self._analyses) > 200 else self._analyses
            analyses_data = [
                {
                    "market_id": a.market_id,
                    "question": a.question,
                    "direction": a.direction,
                    "outcome": a.outcome,
                    "pnl": a.pnl,
                    "edge": a.edge_at_entry,
                    "confluence": a.confluence_at_entry,
                    "root_cause": a.root_cause,
                    "pattern": a.pattern_match,
                    "lessons": a.lessons,
                    "timestamp": a.timestamp,
                }
                for a in recent
            ]
            with open(self.ANALYSIS_FILE, "w") as f:
                json.dump(analyses_data, f, indent=2)

            # Pattern stats kaydet
            pattern_data = {}
            for name, stats in self._patterns.items():
                pattern_data[name] = {
                    "occurrences": stats.occurrences,
                    "wins": stats.wins,
                    "losses": stats.losses,
                    "total_pnl": stats.total_pnl,
                    "avg_pnl": stats.avg_pnl,
                }
            with open(self.PATTERN_FILE, "w") as f:
                json.dump(pattern_data, f, indent=2)

        except Exception as e:
            logger.debug(f"[TradeAnalyzer] Save failed: {e}")

    def get_stats(self) -> dict:
        """Analyzer istatistikleri."""
        total = len(self._analyses)
        wins = sum(1 for a in self._analyses if a.outcome == "WIN")
        losses = sum(1 for a in self._analyses if a.outcome == "LOSS")
        return {
            "total_analyzed": total,
            "win_rate": f"{wins / max(total, 1):.1%}",
            "pattern_count": len(self._patterns),
            "top_patterns": self.get_pattern_report(),
            "recommendations": self.get_recommendations(),
        }
