"""
calibration/types.py

Kalibrasyon katmanının veri modelleri.

Bağımlılık notu: Bu modül signal_bridge.types'a bağımlı değildir.
Polarity bilgisi string olarak iletilir. Bu, kalibrasyon katmanının
bridge değişikliklerinden bağımsız test edilebilmesini sağlar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, Optional


# ── Sabitler ──────────────────────────────────────────────────────────────────

# Desteklenen sinyal ufukları (dakika). Bu set dışındaki horizon_minutes değerleri
# UNSUPPORTED_HORIZON ile reddedilir.
SUPPORTED_HORIZONS: frozenset[int] = frozenset({5, 15})

# Olasılık toplamı politikası (probability_mapper ve decision_policy uygular):
#   toplam < PROB_MIN_SUM      → eksik tanımlı uzay → INCONSISTENT_PROBS
#   toplam > 1 + PROB_MAX_OVER → aşım → INCONSISTENT_PROBS
#   0.50 ≤ toplam ≤ 1.01       → geçerli (NO_TRADE veya yuvarlama boşluğu kabul edilir)
# Live modda class_probabilities zorunlu olduğu için bu policy gerçek model çıktısına
# uygulanır; ham confidence proxy değil.
PROB_MIN_SUM:      float = 0.50   # paper / default: any reasonable spread allowed
PROB_LIVE_MIN_SUM: float = 0.90   # live: near-complete probability mass required
PROB_MAX_OVER:     float = 0.01

# ── Phase 12: Binary market sanity constants ─────────────────────────────────
#
# Hard structural limits (all policy modes — INVALID_PRICING if violated):
#   ask_sum < BINARY_HARD_MIN_ASK_SUM  → data corruption or impossible quotes
#   bid_sum > BINARY_HARD_MAX_BID_SUM  → risk-free arb impossible in real markets
#
# Policy-specific suspicious-underround thresholds (SUSPICIOUS_UNDERROUND):
#   ask_sum below mode threshold → suspicious but not structurally impossible
#   ask_sum above mode max       → excessive vig / stale market
#   bid_sum above mode max       → near-arb territory (live/paper_strict only)
#   individual ask < floor       → one-sided quote pathology

BINARY_HARD_MIN_ASK_SUM: float = 0.85   # hard floor — structural corruption
BINARY_HARD_MAX_BID_SUM: float = 1.01   # hard ceiling — risk-free arb impossible

# Policy-specific ask_sum acceptance bands [min, max]
BINARY_SANITY_MIN_ASK_SUM_LIVE:         float = 0.97
BINARY_SANITY_MAX_ASK_SUM_LIVE:         float = 1.10
BINARY_SANITY_MIN_ASK_SUM_PAPER_STRICT: float = 0.93
BINARY_SANITY_MAX_ASK_SUM_PAPER_STRICT: float = 1.15
BINARY_SANITY_MIN_ASK_SUM_PAPER_LOOSE:  float = 0.88   # annotate only, not rejected
BINARY_SANITY_MAX_ASK_SUM_PAPER_LOOSE:  float = 1.25

# Policy-specific bid_sum ceiling (bid_yes + bid_no ≤ this → OK)
# paper_loose does not enforce bid_sum (check_bid_overround=False)
BINARY_SANITY_MAX_BID_SUM_LIVE:         float = 1.00
BINARY_SANITY_MAX_BID_SUM_PAPER_STRICT: float = 1.00

# Minimum individual ask price (one-sided quote pathology guard)
# e.g. ask_yes=0.02 while ask_no=0.97 → suspicious asymmetry
BINARY_SANITY_MIN_SINGLE_ASK_LIVE:         float = 0.05
BINARY_SANITY_MIN_SINGLE_ASK_PAPER_STRICT: float = 0.03
# paper_loose: no individual check

# Legacy alias kept for any external code that referenced old constant
BINARY_MARKET_MAX_OVERROUND: float = 0.15  # deprecated — use BINARY_SANITY_* above


# ── Sabit diziler ─────────────────────────────────────────────────────────────

class CalibrationMethod(str, Enum):
    IDENTITY  = "identity"   # ham güven = kalibre olasılık (varsayılan proxy)
    PLATT     = "platt"      # sigmoid uyumu — ≥50 örnek gerekir
    ISOTONIC  = "isotonic"   # monotonic regresyon — ≥200 örnek gerekir


class CalibrationQuality(str, Enum):
    STRONG  = "strong"   # ECE < 0.05, ≥200 örnek
    WEAK    = "weak"     # ECE ≥ 0.10 veya az örnek
    UNKNOWN = "unknown"  # identity veya hiç ölçülmedi


class TradeDecisionType(str, Enum):
    EXECUTE_YES = "EXECUTE_YES"
    EXECUTE_NO  = "EXECUTE_NO"
    REJECT      = "REJECT"


class CalibrationRejectionReason(str, Enum):
    NO_TRADE_SIGNAL       = "NO_TRADE_SIGNAL"       # predicted_class == NO_TRADE
    AMBIGUOUS_MAPPING     = "AMBIGUOUS_MAPPING"     # polarity AMBIGUOUS
    WEAK_CALIBRATION      = "WEAK_CALIBRATION"      # kalibrasyon kalitesi yetersiz
    UNKNOWN_CALIBRATION   = "UNKNOWN_CALIBRATION"   # identity/ölçülmemiş — live'da reject
    NEGATIVE_EDGE         = "NEGATIVE_EDGE"         # edge < threshold
    INCONSISTENT_PROBS    = "INCONSISTENT_PROBS"    # prob < 0, prob > 1 veya toplam hatalı
    LOW_CONFIDENCE        = "LOW_CONFIDENCE"        # calibrated_prob < minimum
    LOW_LIQUIDITY         = "LOW_LIQUIDITY"         # piyasa likiditesi yetersiz
    HIGH_SPREAD           = "HIGH_SPREAD"           # spread > maximum
    STALE_PRICING         = "STALE_PRICING"         # fiyat anlık görüntüsü bayat veya gelecekte
    INVALID_PRICING       = "INVALID_PRICING"       # fiyat [0,1] dışı, ask<bid, yapısal bozukluk
    UNSUPPORTED_HORIZON   = "UNSUPPORTED_HORIZON"   # horizon_minutes desteklenmiyor
    PARTIAL_FILL_REJECTED  = "PARTIAL_FILL_REJECTED"   # Phase 11: live mode partial fill rejected
    SUSPICIOUS_UNDERROUND  = "SUSPICIOUS_UNDERROUND"   # Phase 12: binary ask-sum suspicious
    MISSING_SIZE           = "MISSING_SIZE"            # live mode called without intended_size_usdc


# ── Giriş: Ham model çıktısı ──────────────────────────────────────────────────

@dataclass
class RawSignalOutput:
    """
    crypto_directional modülünden gelen ham sinyal.

    class_probabilities None ise yalnızca raw_confidence kullanılır.
    Kalibratör eksik olasılıkları simetrik varsayımla tamamlar:
        calibrated_down_prob ≈ 1 - raw_confidence  (NO_TRADE görmezden gelindi)

    Bu bir yaklaşımdır; class_probabilities sağlanması tercih edilir.
    """
    asset:              str
    horizon_minutes:    int
    timestamp_utc:      datetime
    predicted_class:    str           # "UP" | "DOWN" | "NO_TRADE"
    raw_confidence:     float         # max(predict_proba)
    class_probabilities: Optional[dict[str, float]] = None
    # {"UP": 0.72, "DOWN": 0.18, "NO_TRADE": 0.10}
    model_version:      str           = "v0"
    diagnostics:        Optional[dict] = None

    _VALID_CLASSES: frozenset = frozenset({"UP", "DOWN", "NO_TRADE"})

    def __post_init__(self) -> None:
        if self.predicted_class not in self._VALID_CLASSES:
            raise ValueError(
                f"RawSignalOutput.predicted_class={self.predicted_class!r} is invalid; "
                f"must be one of {sorted(self._VALID_CLASSES)}"
            )
        if not (0.0 <= self.raw_confidence <= 1.0):
            raise ValueError(
                f"RawSignalOutput.raw_confidence={self.raw_confidence} must be in [0, 1]"
            )


# ── Ara: Kalibre edilmiş sinyal ──────────────────────────────────────────────

@dataclass
class CalibratedSignal:
    """
    Kalibrasyon sonrası sinyal.

    effective_yes_prob / effective_no_prob, polarity eşleştirmesi sonucunda
    hesaplanır:
        NORMAL polarity:   effective_yes_prob = calibrated_up_prob
        INVERTED polarity: effective_yes_prob = calibrated_down_prob

    calibration_quality == UNKNOWN → ham güven proxy olarak kullanılıyor.
    Bu, edge hesabının yanlış olabileceği anlamına gelir.
    """
    raw:                      RawSignalOutput
    calibrated_up_prob:       float
    calibrated_down_prob:     float
    calibrated_no_trade_prob: float
    calibration_method:       CalibrationMethod
    calibration_quality:      CalibrationQuality
    # Polarity eşleştirmesi sonucu
    effective_yes_prob:       float
    effective_no_prob:        float
    mapping_context:          str    # "UP→YES (NORMAL)" gibi açıklama
    # Bridge intent: polarity + predicted_class'tan türetilir
    # Karar katmanı YALNIZCA bu tarafı değerlendirir (best-EV side seçimi yok)
    bridge_intent_side:       Literal["YES", "NO"]
    # Opsiyonel kalibrasyon metrikleri (identity'de None)
    brier_score:              Optional[float] = None
    log_loss_val:             Optional[float] = None
    ece:                      Optional[float] = None

    def __post_init__(self) -> None:
        if self.bridge_intent_side not in ("YES", "NO"):
            raise ValueError(
                f"CalibratedSignal.bridge_intent_side={self.bridge_intent_side!r} is invalid; "
                f"must be 'YES' or 'NO'. Downstream manipulation or deserialization error."
            )
        for _name, _val in (
            ("effective_yes_prob", self.effective_yes_prob),
            ("effective_no_prob",  self.effective_no_prob),
        ):
            if not (0.0 <= _val <= 1.0):
                raise ValueError(
                    f"CalibratedSignal.{_name}={_val} must be in [0, 1]"
                )


# ── Fiyat anlık görüntüsü ─────────────────────────────────────────────────────

@dataclass
class MarketPricingSnapshot:
    """
    Polymarket market fiyatı anlık görüntüsü.

    Tüm fiyatlar [0, 1] normalize edilmiş olasılık birimidir.

    spread_yes ve spread_no DERIVED alanlardır — __post_init__ içinde
    ask - bid'den hesaplanır. Dışarıdan geçilen değer görmezden gelinir.
    Bu, sahte spread enjeksiyonunu (snapshot manipulation) önler.

    max_age_seconds: Bu yaşı aşan snapshot'lar STALE_PRICING ile reddedilebilir.
    """
    market_id:          str
    ask_yes:            float
    bid_yes:            float
    ask_no:             float
    bid_no:             float
    liquidity:          float
    timestamp_utc:      datetime
    # spread_yes / spread_no: dışarıdan geçilebilir (eski API uyumluluğu için)
    # ama __post_init__ ask-bid'den yeniden hesaplar — geçilen değer override edilir.
    spread_yes:         float = 0.0
    spread_no:          float = 0.0
    max_age_seconds:    int = 300  # 5 dakika

    def __post_init__(self) -> None:
        # Spread her zaman ask - bid'den türetilir.
        # Dışarıdan gelen spread alanı yok sayılır → manipulation engellenmiş.
        self.spread_yes = self.ask_yes - self.bid_yes
        self.spread_no  = self.ask_no  - self.bid_no


# ── Edge tahmini ──────────────────────────────────────────────────────────────

@dataclass
class EdgeEstimate:
    """
    Tek bir taraf (YES veya NO) için beklenen değer hesabı.

    Math:
        gross_EV           = calibrated_event_probability - market_entry_price
        net_EV             = gross_EV - assumed_cost           (fee sonrası)
        execution_adjusted = net_EV - slippage                 (gerçek maliyet)
        expected_edge      = execution_adjusted                 (karar metriği)

    passes_edge_gate = execution_adjusted_ev >= required_edge_threshold
    """
    side_considered:              str    # "YES" | "NO"
    calibrated_event_probability: float  # P(bu taraf resolve olur)
    market_entry_price:           float  # ask_yes veya ask_no
    assumed_cost:                 float  # taker fee oranı (0.01 = %1)
    gross_expected_value:         float  # p - q
    net_expected_value:           float  # p - q - c (taker fee dahil)
    execution_adjusted_ev:        float  # p - q - c - slippage (gerçekçi EV)
    expected_edge:                float  # = execution_adjusted_ev (karar metriği)
    required_edge_threshold:      float
    passes_edge_gate:             bool
    diagnostics:                  dict   = field(default_factory=dict)
    # Execution realism breakdown — realistic estimator'lar tarafından doldurulur.
    # Constant-slippage yolunda None kalır (deprecated).
    executable_cost_breakdown:    Optional[object] = None  # ExecutableCostBreakdown


# ── Nihai ticaret kararı ──────────────────────────────────────────────────────

@dataclass
class TradeDecision:
    """
    Kalibrasyon katmanının nihai çıktısı.

    decision == REJECT → ticaret açılmaz; rejection_reason dolu
    decision == EXECUTE_YES/NO → edge pozitif, tüm kalibrasyon filtreleri geçildi

    edge_estimate ve calibrated_signal: audit trail ve logging için
    executable_cost_breakdown: Phase 9 execution realism breakdown (opsiyonel)

    Phase 9 audit trail fields:
        theoretical_hold_ev   — p - ask (frictionless upper bound)
        net_ev_after_fee      — theoretical - fee
        final_gate_metric     — always "executable_ev" in Phase 9+
        final_gate_threshold  — config.min_execution_adjusted_edge
        passes_final_gate     — True iff decision is EXECUTE_*
        policy_mode           — "live" | "paper" | "default"
        intended_size_usdc_used — size that was actually used in computation
    """
    decision:                   TradeDecisionType
    rationale:                  str
    rejection_reason:           Optional[CalibrationRejectionReason] = None
    edge_estimate:              Optional[EdgeEstimate] = None
    calibrated_signal:          Optional[CalibratedSignal] = None
    executable_cost_breakdown:  Optional[object] = None  # ExecutableCostBreakdown
    # Phase 9 audit trail fields
    theoretical_hold_ev:        Optional[float] = None   # p - ask (frictionless upper bound)
    net_ev_after_fee:           Optional[float] = None   # theoretical - fee
    final_gate_metric:          str = "executable_ev"    # always "executable_ev" in Phase 9+
    final_gate_threshold:       Optional[float] = None   # config.min_execution_adjusted_edge
    passes_final_gate:          bool = False             # True iff decision is EXECUTE_*
    policy_mode:                str = "unknown"          # "live" | "paper" | "default"
    intended_size_usdc_used:    float = 20.0             # size used in computation
    # Phase 11 partial fill audit fields
    executable_notional_usdc:   Optional[float] = None  # fill_fraction × intended_size_usdc
    fill_fraction:              Optional[float] = None  # expected fill fraction (1.0=FILLABLE)
    # Phase 12 pricing sanity audit fields
    pricing_sanity_notes:       Optional[str]   = None  # set when suspicious underround annotated (paper_loose)


# ── Kalibrasyon konfigürasyonu ─────────────────────────────────────────────────

@dataclass
class CalibrationConfig:
    """
    Kalibrasyon ve edge katmanı için tüm eşikler.

    Bu eşikler bridge_config.py'den bağımsızdır — kalibrasyon katmanı
    kendi filtre setini uygular.
    """
    # Kalibrasyon
    method:                        CalibrationMethod = CalibrationMethod.IDENTITY
    min_calibration_samples:       int   = 50
    weak_ece_threshold:            float = 0.10  # ECE ≥ bu → "weak"
    reject_on_weak_calibration:    bool  = False # Phase 5'e kadar False
    reject_on_unknown_calibration: bool  = False # Canlıda True önerilir

    # Edge
    min_execution_adjusted_edge: float = 0.02  # execution_adjusted_ev için minimum eşik
    assumed_taker_fee_pct:       float = 0.01
    assumed_slippage_pct:        float = 0.005         # fallback: her iki taraf için
    assumed_slippage_yes_pct:    Optional[float] = None  # None → assumed_slippage_pct
    assumed_slippage_no_pct:     Optional[float] = None  # None → assumed_slippage_pct

    # Fiyat kalitesi
    min_calibrated_confidence:   float = 0.55  # effective_prob < bu → reddet
    max_spread_yes:              float = 0.05
    max_spread_no:               float = 0.05
    min_liquidity:               float = 1_000.0
    max_snapshot_age_seconds:    int   = 300   # 5 dakika
    max_snapshot_age_horizon_fraction: Optional[float] = None
    # None → max_snapshot_age_seconds kullan (sabit eşik)
    # float → min(max_snapshot_age_seconds, horizon_minutes * 60 * fraksiyon)

    # Sinyal kalitesi
    require_class_probabilities: bool  = False  # True → class_probabilities zorunlu

    # Probability sum contract at decision gate (independent of PROB_MIN_SUM mapper constant)
    # paper/default: 0.50 allows sparse class_probabilities
    # live: 0.90 requires near-complete probability mass (raw confidence proxy forbidden)
    min_prob_sum: float = PROB_MIN_SUM

    # Phase 12: Binary market sanity — policy-specific thresholds.
    # None → use mode-based defaults from BINARY_SANITY_* constants.
    # Set explicitly to override for custom configs.
    min_ask_sum_binary:      Optional[float] = None  # None → mode default (0.97/0.93/0.88)
    max_ask_sum_binary:      Optional[float] = None  # None → mode default (1.10/1.15/1.25)
    check_bid_overround:     bool            = True  # paper_loose: False
    max_bid_sum_binary:      Optional[float] = None  # None → mode default (1.00/1.00/N/A)
    min_single_ask_binary:   Optional[float] = None  # None → mode default (0.05/0.03/None)
    # True → suspicious underround is annotated but does NOT cause rejection (paper_loose).
    # False → suspicious underround causes REJECT with SUSPICIOUS_UNDERROUND (live/paper_strict).
    allow_suspicious_underround: bool = False

    # Politika modu — karar davranışını etkiler (Phase 12: live|paper_strict|paper_loose|paper|default)
    mode: str = "default"  # "live" | "paper_strict" | "paper_loose" | "paper" | "default"

    def __post_init__(self) -> None:
        if not (0.0 <= self.min_calibrated_confidence <= 1.0):
            raise ValueError(
                f"min_calibrated_confidence={self.min_calibrated_confidence} must be in [0,1]"
            )
        if self.min_execution_adjusted_edge < 0:
            raise ValueError(
                f"min_execution_adjusted_edge={self.min_execution_adjusted_edge} must be >= 0"
            )
        if not (0.0 <= self.assumed_taker_fee_pct < 1.0):
            raise ValueError(
                f"assumed_taker_fee_pct={self.assumed_taker_fee_pct} must be in [0,1)"
            )
        if not (0.0 <= self.min_prob_sum <= 1.0):
            raise ValueError(
                f"min_prob_sum={self.min_prob_sum} must be in [0,1]"
            )
        # Phase 12: validate optional override thresholds
        _min_ask = self.min_ask_sum_binary
        if _min_ask is not None and not (0.0 < _min_ask < 1.5):
            raise ValueError(f"min_ask_sum_binary={_min_ask} must be in (0, 1.5)")
        _max_ask = self.max_ask_sum_binary
        if _max_ask is not None and not (0.5 < _max_ask <= 2.0):
            raise ValueError(f"max_ask_sum_binary={_max_ask} must be in (0.5, 2.0]")


DEFAULT_CAL_CONFIG = CalibrationConfig()

# ── Phase 12 policy profiles ──────────────────────────────────────────────────
#
# Three explicit policy profiles:
#
#   LIVE            — real capital; strictest on every axis
#   PAPER_STRICT    — honest research; close to live; rejects what live rejects
#   PAPER_LOOSE     — exploratory; annotates suspicious pricing but allows through
#
# See PAPER_MODE_POLICY.md and config/policies.py for full comparison table.

# Live (gerçek para):
#   - UNKNOWN/WEAK calibration rejected
#   - require_class_probabilities: ham güven proxy yasak
#   - min_prob_sum ≥ 0.90 (near-complete probability mass)
#   - min_ask_sum ≥ 0.97, max_ask_sum ≤ 1.10 (tight binary sanity)
#   - bid_sum checked ≤ 1.00
#   - min_single_ask ≥ 0.05 (pathological quote guard)
#   - allow_suspicious_underround=False → REJECT with SUSPICIOUS_UNDERROUND
#   - max_snapshot_age=60s
#   - min_execution_adjusted_edge=0.030
LIVE_CAL_CONFIG = CalibrationConfig(
    reject_on_unknown_calibration=True,
    reject_on_weak_calibration=True,
    min_execution_adjusted_edge=0.030,
    require_class_probabilities=True,
    min_prob_sum=PROB_LIVE_MIN_SUM,          # 0.90
    max_snapshot_age_seconds=60,
    allow_suspicious_underround=False,
    check_bid_overround=True,
    mode="live",
)

# Paper strict (honest research):
#   - UNKNOWN/WEAK calibration rejected (same as live)
#   - min_prob_sum ≥ 0.75 (stricter than loose, looser than live)
#   - min_ask_sum ≥ 0.93, max_ask_sum ≤ 1.15
#   - bid_sum checked ≤ 1.00
#   - min_single_ask ≥ 0.03
#   - allow_suspicious_underround=False → REJECT
#   - max_snapshot_age=120s
#   - min_execution_adjusted_edge=0.025
PAPER_STRICT_CAL_CONFIG = CalibrationConfig(
    reject_on_unknown_calibration=True,
    reject_on_weak_calibration=True,
    min_execution_adjusted_edge=0.025,
    require_class_probabilities=False,
    min_prob_sum=0.75,
    max_snapshot_age_seconds=120,
    allow_suspicious_underround=False,
    check_bid_overround=True,
    mode="paper_strict",
)

# Paper loose (exploratory / observational):
#   - UNKNOWN/WEAK calibration passes (research convenience)
#   - min_prob_sum ≥ 0.50 (sparse class_probs allowed)
#   - min_ask_sum ≥ 0.88, max_ask_sum ≤ 1.25
#   - bid_sum NOT checked (check_bid_overround=False)
#   - no individual ask floor
#   - allow_suspicious_underround=True → annotate in pricing_sanity_notes, do NOT reject
#   - max_snapshot_age=300s (default)
#   - min_execution_adjusted_edge=0.020
PAPER_LOOSE_CAL_CONFIG = CalibrationConfig(
    reject_on_unknown_calibration=False,
    reject_on_weak_calibration=False,
    min_execution_adjusted_edge=0.020,
    require_class_probabilities=False,
    min_prob_sum=PROB_MIN_SUM,               # 0.50
    max_snapshot_age_seconds=300,
    allow_suspicious_underround=True,
    check_bid_overround=False,
    mode="paper_loose",
)

# Backward-compatible legacy alias — mode="paper" preserved so existing tests
# that assert result.policy_mode == "paper" continue to pass.
# Behaviour is identical to PAPER_LOOSE_CAL_CONFIG.
# New code should use PAPER_LOOSE_CAL_CONFIG or PAPER_STRICT_CAL_CONFIG explicitly.
PAPER_CAL_CONFIG = CalibrationConfig(
    reject_on_unknown_calibration=False,
    reject_on_weak_calibration=False,
    min_execution_adjusted_edge=0.020,
    require_class_probabilities=False,
    min_prob_sum=PROB_MIN_SUM,
    max_snapshot_age_seconds=300,
    allow_suspicious_underround=True,
    check_bid_overround=False,
    mode="paper",
)
