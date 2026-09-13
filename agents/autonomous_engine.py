"""
AutonomousDecisionEngine — Kendi kendini yöneten otonom karar motoru.

Claude Code auto mode felsefesinden ilham alır:
- Her eylem risk seviyesine göre otomatik sınıflandırılır
- Düşük riskli işlemler otomatik geçer
- Yüksek riskli olanlarda ek analiz + koruma devreye girer
- Bot ASLA durmaz — her hata graceful degrade ile yönetilir

Karar Seviyeleri:
    LOW_RISK  → Otomatik onayla (market scan, veri toplama, küçük trade)
    MED_RISK  → Analiz et, devam et (orta büyüklük trade, bilinmeyen market)
    HIGH_RISK → Derin analiz, pozisyon küçült (büyük trade, volatil market)
    CRITICAL  → Minimum pozisyon, ekstra güvenlik (streak loss, düşük sermaye)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ActionType(str, Enum):
    EXECUTE = "EXECUTE"           # Trade'i yap
    EXECUTE_REDUCED = "REDUCED"   # Küçültülmüş pozisyonla yap
    SKIP = "SKIP"                 # Bu döngüde atla
    DEFER = "DEFER"               # Sonraki döngüye ertele


@dataclass
class AutonomousDecision:
    """Motor'un her sinyal için ürettiği karar."""
    action: ActionType
    risk_level: RiskLevel
    size_multiplier: float = 1.0      # 0.1 - 1.0 arası pozisyon çarpanı
    reasoning: list[str] = field(default_factory=list)
    confidence: float = 0.5
    meta: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def should_execute(self) -> bool:
        return self.action in (ActionType.EXECUTE, ActionType.EXECUTE_REDUCED)

    def summary(self) -> str:
        reasons = " | ".join(self.reasoning) if self.reasoning else "No specific reason"
        return (
            f"[{self.risk_level.value}] {self.action.value} "
            f"(size×{self.size_multiplier:.2f}, conf={self.confidence:.2f}) — {reasons}"
        )


@dataclass
class PerformanceSnapshot:
    """Bot'un anlık performans durumu."""
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_edge: float = 0.0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    capital: float = 0.0
    initial_capital: float = 0.0
    peak_capital: float = 0.0
    drawdown_pct: float = 0.0
    hourly_pnl: float = 0.0
    session_pnl: float = 0.0
    volatility_regime: str = "NORMAL"  # LOW, NORMAL, HIGH, EXTREME


class AutonomousDecisionEngine:
    """
    Botun beyni — her trade kararını bağımsız değerlendirir.

    Prensipler:
    1. Bot ASLA durmamalı — her hata graceful handle edilir
    2. Risk seviyesine göre otomatik pozisyon boyutlandırma
    3. Performans bazlı adaptif davranış (iyi gidiyorsa agresif, kötü gidiyorsa savunma)
    4. Her trade sonrası öğrenme — pattern'ları takip et
    """

    # ── Konfigürasyon ──
    PERSISTENCE_FILE = Path("data/autonomous_state.json")

    # Risk eşikleri
    LOW_RISK_EDGE = 0.12       # Bu üzerinde edge = düşük risk
    MED_RISK_EDGE = 0.08       # Bu üzerinde ama LOW altı = orta risk
    HIGH_RISK_FLAGS = 3         # 3+ risk flag = yüksek risk

    # Performans bazlı çarpanlar
    STREAK_LOSS_THRESHOLD = 2   # 2 ardışık kayıp → savunma modu
    STREAK_WIN_THRESHOLD = 4    # 4 ardışık kazanç → dikkatli agresiflik
    DRAWDOWN_DANGER = 0.90      # %90 drawdown → kritik mod (eski: 0.25, gevşetildi — tarihi drawdown trade'i engellemsin)
    LOW_CAPITAL_THRESHOLD = 10  # $10 altı → survival mode

    def __init__(self):
        self._state = self._load_state()
        self._session_start = time.time()
        self._decisions_this_session: list[AutonomousDecision] = []
        self._performance = PerformanceSnapshot()
        logger.info("[AutonomousEngine] Initialized — autonomous decision making active")

    # ═══════════════════════════════════════════════════════════════════
    # ANA KARAR FONKSİYONU
    # ═══════════════════════════════════════════════════════════════════

    def evaluate(
        self,
        signal: Any,
        review_decision: Any,
        capital: float,
        open_count: int,
        max_positions: int,
        closed_trades: list[dict] | None = None,
    ) -> AutonomousDecision:
        """
        Bir sinyal için otonom karar üret.

        Sıra:
        1. Performans snapshot güncelle
        2. Risk seviyesi belirle
        3. Adaptif pozisyon boyutu hesapla
        4. Final karar ver

        Args:
            signal: EnrichedSignal — arbitrage engine sinyali
            review_decision: ReviewDecision — reviewer agent kararı
            capital: Mevcut sermaye
            open_count: Açık pozisyon sayısı
            max_positions: Max izin verilen pozisyon
            closed_trades: Geçmiş trade'ler (performans analizi için)

        Returns:
            AutonomousDecision
        """
        reasons = []

        # 1. Performans snapshot güncelle
        if closed_trades:
            self._update_performance(closed_trades, capital)

        # 2. Risk seviyesi belirle
        risk_level = self._assess_risk(signal, review_decision, capital, open_count, max_positions)
        reasons.append(f"Risk: {risk_level.value}")

        # 3. Temel karar: execute vs skip
        action = ActionType.EXECUTE
        size_mult = 1.0
        confidence = getattr(signal, 'confluence_score', 0.5)

        # ── CRITICAL MODE: Sermaye kritik düşük ──
        if capital < self.LOW_CAPITAL_THRESHOLD:
            risk_level = RiskLevel.CRITICAL
            size_mult = 0.3
            reasons.append(f"SURVIVAL_MODE: capital=${capital:.2f} < ${self.LOW_CAPITAL_THRESHOLD}")

        # ── DRAWDOWN KORUMASI ──
        if self._performance.drawdown_pct > self.DRAWDOWN_DANGER:
            risk_level = RiskLevel.CRITICAL
            size_mult = min(size_mult, 0.6)
            reasons.append(f"DRAWDOWN: {self._performance.drawdown_pct:.1%} > {self.DRAWDOWN_DANGER:.0%}")

        # ── STREAK LOSS SAVUNMA ──
        if self._performance.consecutive_losses >= self.STREAK_LOSS_THRESHOLD:
            streak = self._performance.consecutive_losses
            # AGGRESSIVE MODE: daha az fren
            loss_mult = max(0.50, 1.0 - (streak * 0.15))
            size_mult = min(size_mult, loss_mult)
            reasons.append(f"LOSS_STREAK({streak}): size×{loss_mult:.2f}")

            # 4+ ardışık kayıp → edge < 0.08 ise skip (gevşetildi: 0.15→0.08)
            if streak >= 4 and getattr(signal, 'edge', 0) < 0.08:
                action = ActionType.SKIP
                reasons.append(f"STREAK_FILTER: {streak} kayıp + edge={getattr(signal, 'edge', 0):.3f} < 0.08")

        # ── STREAK WIN DİKKAT ──
        if self._performance.consecutive_wins >= self.STREAK_WIN_THRESHOLD:
            # Aşırı güvenlenme tehlikesi — biraz fren yap
            size_mult = min(size_mult, 0.85)
            reasons.append(f"WIN_STREAK_CAUTION({self._performance.consecutive_wins}): overconfidence guard")

        # ── RISK SEVİYESİNE GÖRE BOYUT AYARI ──
        if risk_level == RiskLevel.LOW:
            size_mult = min(size_mult, 1.0)
            reasons.append("LOW_RISK: full size")
        elif risk_level == RiskLevel.MEDIUM:
            size_mult = min(size_mult, 0.75)
            reasons.append("MED_RISK: size×0.75")
        elif risk_level == RiskLevel.HIGH:
            size_mult = min(size_mult, 0.50)
            action = ActionType.EXECUTE_REDUCED
            reasons.append("HIGH_RISK: size×0.50")
        elif risk_level == RiskLevel.CRITICAL:
            size_mult = min(size_mult, 0.50)
            action = ActionType.EXECUTE_REDUCED if action != ActionType.SKIP else action
            reasons.append("CRITICAL: size×0.50")

        # ── REVIEWER VERDICT ENTEGRASYONU ──
        verdict_val = getattr(review_decision, 'verdict', None)
        if verdict_val:
            verdict_str = verdict_val.value if hasattr(verdict_val, 'value') else str(verdict_val)
            if verdict_str == "VETO":
                # Veto'yu tamamen atlama — sadece agresif küçült
                # Bot durmadan devam etmeli ama veto'd trade'e dikkat
                size_mult = min(size_mult, 0.25)
                action = ActionType.EXECUTE_REDUCED
                reasons.append("REVIEWER_VETO: severely reduced but not skipped")
            elif verdict_str == "REDUCE":
                # NOTE: do NOT also fold `suggested` into size_mult here.
                # AgentCoordinator.run_cycle() (agents/subagents/coordinator.py)
                # already multiplies signal.size by suggested_size_pct before
                # this signal ever reaches the orchestrator's per-signal loop.
                # bet_size is later derived from that already-reduced
                # signal.size (compute_bet_size(signal_size=signal.size, ...)),
                # so re-applying `suggested` to size_mult here would shrink the
                # live bet_size by suggested_size_pct² instead of the reviewer's
                # intended suggested_size_pct (same double-application pattern
                # as FIX_CONFLICT_REPORT.md's 15m double-dampening bug).
                suggested = getattr(review_decision, 'suggested_size_pct', 0.6)
                action = ActionType.EXECUTE_REDUCED
                reasons.append(f"REVIEWER_REDUCE: size already ×{suggested:.2f} via coordinator")

        # ── VOLATİLİTE REJİMİ ──
        regime_strength = getattr(signal, 'regime_strength', 0.0)
        if regime_strength > 0.80:
            size_mult = min(size_mult, 0.5)
            reasons.append(f"HIGH_VOLATILITY: regime={regime_strength:.2f}")

        # ── EDGE BAZLI KALİTE ──
        edge = getattr(signal, 'edge', 0)
        if edge > 0.20:
            size_mult = min(size_mult * 1.2, 1.0)  # Yüksek edge bonus (max 1.0)
            reasons.append(f"HIGH_EDGE_BONUS: edge={edge:.3f}")
        elif edge < 0.05:
            action = ActionType.SKIP
            reasons.append(f"EDGE_TOO_LOW: {edge:.3f} < 0.05")

        # ── ZAMAN BAZLI ADAPTASYON ──
        # Gece saatleri (UTC 0-6) → düşük likidite, küçült
        hour_utc = time.gmtime().tm_hour
        if 0 <= hour_utc < 6:
            size_mult = min(size_mult, 0.7)
            reasons.append(f"LOW_LIQUIDITY_HOURS: UTC {hour_utc}")

        # Final karar
        decision = AutonomousDecision(
            action=action,
            risk_level=risk_level,
            size_multiplier=max(0.1, min(1.0, size_mult)),  # 0.1-1.0 arası clamp
            reasoning=reasons,
            confidence=confidence,
            meta={
                "edge": edge,
                "capital": capital,
                "open_count": open_count,
                "perf_wr": self._performance.win_rate,
                "consec_loss": self._performance.consecutive_losses,
                "drawdown": self._performance.drawdown_pct,
            },
        )

        self._decisions_this_session.append(decision)
        self._save_state()

        logger.info(f"[AutonomousEngine] {decision.summary()}")
        return decision

    # ═══════════════════════════════════════════════════════════════════
    # RİSK DEĞERLENDİRME
    # ═══════════════════════════════════════════════════════════════════

    def _assess_risk(
        self,
        signal: Any,
        review_decision: Any,
        capital: float,
        open_count: int,
        max_positions: int,
    ) -> RiskLevel:
        """Çoklu faktöre dayalı risk sınıflandırması."""
        risk_score = 0  # 0-10 arası

        edge = getattr(signal, 'edge', 0)
        direction = getattr(signal, 'direction', 'YES')
        risk_flags = getattr(signal, 'risk_flags', [])
        confluence = getattr(signal, 'confluence_score', 0.5)

        # Edge faktörü (düşük edge = yüksek risk)
        if edge < 0.05:
            risk_score += 3
        elif edge < 0.08:
            risk_score += 2
        elif edge < 0.12:
            risk_score += 1

        # Yön faktörü (NO daha riskli)
        if direction == "NO":
            risk_score += 2  # NO trades historically 33% WR

        # Risk flag sayısı
        risk_score += min(len(risk_flags), 3)

        # Confluence (düşük = riskli)
        if confluence < 0.3:
            risk_score += 2
        elif confluence < 0.5:
            risk_score += 1

        # Pozisyon yoğunluğu
        if open_count >= max_positions - 1:
            risk_score += 1

        # Sermaye durumu
        if capital < 20:
            risk_score += 2
        elif capital < 50:
            risk_score += 1

        # Skor → seviye
        if risk_score <= 2:
            return RiskLevel.LOW
        elif risk_score <= 4:
            return RiskLevel.MEDIUM
        elif risk_score <= 7:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL

    # ═══════════════════════════════════════════════════════════════════
    # PERFORMANS TAKİBİ
    # ═══════════════════════════════════════════════════════════════════

    def _update_performance(self, closed_trades: list[dict], capital: float):
        """Kapalı trade'lerden performans snapshot güncelle."""
        if not closed_trades:
            return

        perf = self._performance
        perf.total_trades = len(closed_trades)
        perf.capital = capital
        perf.initial_capital = float(os.getenv("INITIAL_CAPITAL", 500))

        # Win/Loss say
        wins = [t for t in closed_trades if t.get("pnl", 0) > 0]
        losses = [t for t in closed_trades if t.get("pnl", 0) <= 0]
        perf.win_count = len(wins)
        perf.loss_count = len(losses)
        perf.win_rate = perf.win_count / max(perf.total_trades, 1)

        # Ortalama edge
        edges = [t.get("edge", 0) for t in closed_trades if t.get("edge")]
        perf.avg_edge = sum(edges) / max(len(edges), 1)

        # Ardışık kayıp/kazanç
        perf.consecutive_losses = 0
        perf.consecutive_wins = 0
        for t in reversed(closed_trades):
            pnl = t.get("pnl", 0)
            if pnl > 0:
                if perf.consecutive_losses == 0:
                    perf.consecutive_wins += 1
                else:
                    break
            elif pnl <= 0:
                if perf.consecutive_wins == 0:
                    perf.consecutive_losses += 1
                else:
                    break

        # Drawdown — measured from the true session high-water mark, not just
        # initial_capital vs current capital. peak_capital is a running max
        # across every _update_performance() call this session, so a drop
        # from a post-growth peak (e.g. $1000 -> $3000 -> $1500) is still
        # detected even though current capital ($1500) never falls below
        # initial_capital ($1000) — previously that case always read as 0%
        # drawdown, silently defeating the CRITICAL drawdown protection below.
        perf.peak_capital = max(perf.peak_capital, perf.initial_capital, capital)
        if perf.peak_capital > 0:
            perf.drawdown_pct = max(0, (perf.peak_capital - capital) / perf.peak_capital)

        # Session PnL
        pnls = [t.get("pnl", 0) for t in closed_trades]
        perf.session_pnl = sum(pnls)

        # Son 1 saatteki PnL
        hour_ago = time.time() - 3600
        recent_pnl = sum(
            t.get("pnl", 0) for t in closed_trades
            if t.get("closed_at", 0) > hour_ago or t.get("ts", 0) > hour_ago
        )
        perf.hourly_pnl = recent_pnl

    # ═══════════════════════════════════════════════════════════════════
    # ADAPTİF PARAMETRE AYARLAMA
    # ═══════════════════════════════════════════════════════════════════

    def get_adaptive_params(self) -> dict:
        """
        Performansa göre dinamik parametre önerileri.
        Orchestrator bu değerleri okuyup uygulayabilir.
        """
        perf = self._performance
        params = {
            "min_edge_yes": 0.08,
            "min_edge_no": 0.15,
            "max_bet_multiplier": 1.0,
            "cycle_interval_seconds": 60,
            "aggression": "NORMAL",
        }

        # Win rate yüksekse → biraz daha agresif
        if perf.win_rate > 0.65 and perf.total_trades > 10:
            params["max_bet_multiplier"] = 1.15
            params["min_edge_yes"] = 0.06
            params["aggression"] = "AGGRESSIVE"

        # Win rate düşükse → savunmacı
        elif perf.win_rate < 0.40 and perf.total_trades > 10:
            params["max_bet_multiplier"] = 0.6
            params["min_edge_yes"] = 0.10
            params["min_edge_no"] = 0.20
            params["aggression"] = "DEFENSIVE"

        # Sermaye kritik → survival
        if perf.capital < self.LOW_CAPITAL_THRESHOLD:
            params["max_bet_multiplier"] = 0.3
            params["min_edge_yes"] = 0.12
            params["min_edge_no"] = 0.25
            params["aggression"] = "SURVIVAL"

        # Ardışık kayıp → hızlı devam (cycle_interval kısalt), ama "aggression"
        # etiketini SADECE hâlâ NORMAL ise "AGGRESSIVE"ye çevir — DEFENSIVE/
        # SURVIVAL zaten yukarıda perf.win_rate/perf.capital'a göre set edildiyse
        # (gerçek min_edge/max_bet_multiplier sıkılaştırması hâlâ yürürlükte),
        # bu blok onu ezip günlüklerde "AGGRESSIVE" göstermemeli — orchestrator.run()
        # bu label'ı sadece logluyor (ADAPTIVE_INTERVAL/ADAPTIVE_RISK), gerçek
        # min_edge_yes/no ve max_bet_multiplier değerleri etkilenmiyor, ama yanlış
        # etiket bir sonraki günlük incelemeyi (bu döngü) yanıltabilir.
        if perf.consecutive_losses >= 3:
            params["cycle_interval_seconds"] = 60  # 1dk (AGGRESSIVE: 120→60)
            if params["aggression"] == "NORMAL":
                params["aggression"] = "AGGRESSIVE"

        return params

    # ═══════════════════════════════════════════════════════════════════
    # DURUM YÖNETİMİ
    # ═══════════════════════════════════════════════════════════════════

    def _load_state(self) -> dict:
        """Kalıcı durumu yükle."""
        try:
            if self.PERSISTENCE_FILE.exists():
                with open(self.PERSISTENCE_FILE) as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"[AutonomousEngine] State load failed: {e}")
        return {
            "total_decisions": 0,
            "total_executes": 0,
            "total_skips": 0,
            "session_history": [],
        }

    def _save_state(self):
        """Durumu diske kaydet."""
        try:
            self.PERSISTENCE_FILE.parent.mkdir(exist_ok=True)
            self._state["total_decisions"] = len(self._decisions_this_session)
            self._state["total_executes"] = sum(
                1 for d in self._decisions_this_session if d.should_execute
            )
            self._state["total_skips"] = sum(
                1 for d in self._decisions_this_session if not d.should_execute
            )
            self._state["last_update"] = time.time()

            with open(self.PERSISTENCE_FILE, "w") as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            logger.debug(f"[AutonomousEngine] State save failed: {e}")

    def get_stats(self) -> dict:
        """Motor istatistikleri."""
        return {
            "session_decisions": len(self._decisions_this_session),
            "session_executes": sum(1 for d in self._decisions_this_session if d.should_execute),
            "session_skips": sum(1 for d in self._decisions_this_session if not d.should_execute),
            "performance": {
                "win_rate": self._performance.win_rate,
                "total_trades": self._performance.total_trades,
                "consecutive_losses": self._performance.consecutive_losses,
                "drawdown": self._performance.drawdown_pct,
                "capital": self._performance.capital,
            },
            "adaptive_params": self.get_adaptive_params(),
        }
