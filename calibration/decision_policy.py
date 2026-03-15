"""
calibration/decision_policy.py

Kural tabanlı, deterministik ticaret karar katmanı.

Giriş:  CalibratedSignal + MarketPricingSnapshot + CalibrationConfig
Çıktı:  TradeDecision (EXECUTE_YES / EXECUTE_NO / REJECT)

Karar sırası (12 adım)
----------------------
1.  Kalibrasyon kalitesi — WEAK    (reject_on_weak_calibration=True ise)
2.  Kalibrasyon kalitesi — UNKNOWN  (reject_on_unknown_calibration=True ise)
3.  class_probabilities zorunluluğu (require_class_probabilities=True ise)
4.  Probability boundary contract   (INCONSISTENT_PROBS — mapper'a güvenmez)
5.  Horizon kontrolü               (SUPPORTED_HORIZONS dışında → UNSUPPORTED_HORIZON)
6.  Pricing snapshot yapısal doğrulaması (fiyat aralığı, ask>=bid, binary sanity)
7.  Pricing snapshot yaşı kontrolü (opsiyonel: horizon-aware)
8.  Likidite kontrolü
9.  Bridge-intent side doğrulaması  (Literal["YES","NO"] dışında → AMBIGUOUS_MAPPING)
10. Spread kontrolü (side-aware; spread derived = ask - bid)
11. Minimum kalibre olasılık kontrolü
12. Edge eşiği — execution_adjusted_ev >= min_execution_adjusted_edge
    (execution_realism ile gerçekçi maliyet: slippage + staleness + fill sim)
→ Tüm kontroller geçilirse EXECUTE; aksi REJECT

Tasarım ilkeleri:
- Sessiz fallback yok — her ret açık bir CalibrationRejectionReason taşır
- Threshold'lar tamamen config-driven
- Deterministik — aynı girdi her zaman aynı çıktı
- LLM/AI karar mantığı yok
- Ana edge yolu: estimate_*_edge_realistic() — executable EV karar metriği
- Constant-slippage estimator'lar deprecated (backward-compat testler hariç)
"""
from __future__ import annotations

from datetime import datetime, timezone

from calibration.edge_estimator import (
    estimate_no_edge_realistic,
    estimate_yes_edge_realistic,
)
from calibration.types import (
    BINARY_HARD_MAX_BID_SUM,
    BINARY_HARD_MIN_ASK_SUM,
    BINARY_SANITY_MAX_ASK_SUM_LIVE,
    BINARY_SANITY_MAX_ASK_SUM_PAPER_LOOSE,
    BINARY_SANITY_MAX_ASK_SUM_PAPER_STRICT,
    BINARY_SANITY_MAX_BID_SUM_LIVE,
    BINARY_SANITY_MAX_BID_SUM_PAPER_STRICT,
    BINARY_SANITY_MIN_ASK_SUM_LIVE,
    BINARY_SANITY_MIN_ASK_SUM_PAPER_LOOSE,
    BINARY_SANITY_MIN_ASK_SUM_PAPER_STRICT,
    BINARY_SANITY_MIN_SINGLE_ASK_LIVE,
    BINARY_SANITY_MIN_SINGLE_ASK_PAPER_STRICT,
    CalibratedSignal,
    CalibrationConfig,
    CalibrationQuality,
    CalibrationRejectionReason,
    DEFAULT_CAL_CONFIG,
    MarketPricingSnapshot,
    PROB_MAX_OVER,
    SUPPORTED_HORIZONS,
    TradeDecision,
    TradeDecisionType,
)
from execution_realism.types import FillDecision


def decide(
    cal_signal: CalibratedSignal,
    pricing: MarketPricingSnapshot,
    config: CalibrationConfig = DEFAULT_CAL_CONFIG,
    now_utc: datetime | None = None,
    intended_size_usdc: float | None = None,
) -> TradeDecision:
    """
    Kalibrasyon + fiyat bilgisi → TradeDecision.

    Parametreler
    ------------
    cal_signal          : probability_mapper çıktısı
    pricing             : anlık market fiyatları
    config              : kalibrasyon konfigürasyonu
    now_utc             : geçerli zaman (None → datetime.now(UTC))
    intended_size_usdc  : pozisyon büyüklüğü (USDC).
                          Live modda None geçmek ValueError fırlatır.
                          Paper/default: None → 20.0 USDC varsayılan.

    Döndürür
    --------
    TradeDecision:
    - EXECUTE_YES / EXECUTE_NO: ticaret açılabilir
    - REJECT: rejection_reason ile birlikte
    """
    # _size: 0.0 sentinel used only if live mode rejects for missing size below.
    _size: float = intended_size_usdc if intended_size_usdc is not None else 20.0

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    def _reject(reason: CalibrationRejectionReason, note: str) -> TradeDecision:
        # Phase 11: fixed audit trail — steps 1-9 now carry correct policy_mode and size.
        return TradeDecision(
            decision=TradeDecisionType.REJECT,
            rationale=note,
            rejection_reason=reason,
            calibrated_signal=cal_signal,
            policy_mode=config.mode,
            intended_size_usdc_used=_size,
        )

    # 0. Live mode requires explicit intended_size_usdc — structured REJECT, not exception.
    #    Raises no exception so orchestrators receive a journalable, auditable record.
    if config.mode == "live" and intended_size_usdc is None:
        return _reject(
            CalibrationRejectionReason.MISSING_SIZE,
            "REJECT: config.mode='live' requires explicit intended_size_usdc. "
            "Pass the actual trade size (e.g. intended_size_usdc=50.0).",
        )

    # 1. Kalibrasyon kalitesi — WEAK
    if (
        config.reject_on_weak_calibration
        and cal_signal.calibration_quality == CalibrationQuality.WEAK
    ):
        return _reject(
            CalibrationRejectionReason.WEAK_CALIBRATION,
            "REJECT: calibration_quality=WEAK ve reject_on_weak_calibration=True",
        )

    # 2. Kalibrasyon kalitesi — UNKNOWN
    if (
        config.reject_on_unknown_calibration
        and cal_signal.calibration_quality == CalibrationQuality.UNKNOWN
    ):
        return _reject(
            CalibrationRejectionReason.UNKNOWN_CALIBRATION,
            "REJECT: calibration_quality=UNKNOWN ve reject_on_unknown_calibration=True",
        )

    # 3. class_probabilities zorunluluğu
    if config.require_class_probabilities and cal_signal.raw.class_probabilities is None:
        return _reject(
            CalibrationRejectionReason.UNKNOWN_CALIBRATION,
            "REJECT: require_class_probabilities=True ama class_probabilities=None; "
            "ham güven proxy canlı modda yasak",
        )

    # 4. Probability boundary contract — mapper'a güvenmez; decision gate bağımsız doğrular.
    #    Upstream bug ya da elle kurulmuş nesne gelirse bu adım yakalar.
    _p_up   = cal_signal.calibrated_up_prob
    _p_down = cal_signal.calibrated_down_prob
    _p_nt   = cal_signal.calibrated_no_trade_prob
    for _pname, _pval in (
        ("calibrated_up_prob",       _p_up),
        ("calibrated_down_prob",     _p_down),
        ("calibrated_no_trade_prob", _p_nt),
    ):
        if not (0.0 <= _pval <= 1.0):
            return _reject(
                CalibrationRejectionReason.INCONSISTENT_PROBS,
                f"REJECT: {_pname}={_pval:.4f} [0,1] dışında (boundary check)",
            )
    _total = _p_up + _p_down + _p_nt
    if _total > 1.0 + PROB_MAX_OVER:
        return _reject(
            CalibrationRejectionReason.INCONSISTENT_PROBS,
            f"REJECT: probability toplam={_total:.4f} > 1+{PROB_MAX_OVER} (boundary check)",
        )
    if _total < config.min_prob_sum:
        return _reject(
            CalibrationRejectionReason.INCONSISTENT_PROBS,
            f"REJECT: probability toplam={_total:.4f} < {config.min_prob_sum} (boundary check)",
        )

    # 5. Horizon kontrolü
    horizon = cal_signal.raw.horizon_minutes
    if horizon not in SUPPORTED_HORIZONS:
        return _reject(
            CalibrationRejectionReason.UNSUPPORTED_HORIZON,
            f"REJECT: horizon_minutes={horizon} desteklenmiyor; "
            f"desteklenen: {sorted(SUPPORTED_HORIZONS)}",
        )

    # 6. Pricing snapshot yapısal doğrulaması
    pricing_err = _validate_pricing_snapshot(pricing, now_utc)
    if pricing_err is not None:
        return _reject(
            CalibrationRejectionReason.INVALID_PRICING,
            pricing_err,
        )

    # 6b. Binary market sanity — policy-specific suspicious underround check.
    #     Runs after structural validation (step 6) but before staleness/liquidity gates.
    #     live / paper_strict: REJECT with SUSPICIOUS_UNDERROUND.
    #     paper_loose / paper / default: annotate in pricing_sanity_notes, do not reject.
    _sanity_note: str | None = None
    _sanity_msg = _check_binary_sanity(pricing, config)
    if _sanity_msg is not None:
        if not config.allow_suspicious_underround:
            return _reject(CalibrationRejectionReason.SUSPICIOUS_UNDERROUND, _sanity_msg)
        else:
            _sanity_note = _sanity_msg

    # 7. Pricing snapshot yaşı (opsiyonel: horizon-aware)
    #    snapshot_age buradan sonra realistic estimator'a da geçilir.
    snapshot_age = _snapshot_age_seconds(pricing.timestamp_utc, now_utc)
    max_age = _effective_max_age(config, cal_signal.raw.horizon_minutes)
    if snapshot_age > max_age:
        return _reject(
            CalibrationRejectionReason.STALE_PRICING,
            f"REJECT: pricing snapshot yaşı={snapshot_age:.0f}s > "
            f"max={max_age:.0f}s (horizon={cal_signal.raw.horizon_minutes}dk)",
        )

    # 8. Likidite
    if pricing.liquidity < config.min_liquidity:
        return _reject(
            CalibrationRejectionReason.LOW_LIQUIDITY,
            f"REJECT: liquidity={pricing.liquidity:.0f} < "
            f"min={config.min_liquidity:.0f}",
        )

    # 9. Bridge-intent side doğrulaması
    intent = cal_signal.bridge_intent_side
    if intent not in ("YES", "NO"):
        return _reject(
            CalibrationRejectionReason.AMBIGUOUS_MAPPING,
            f"REJECT: bridge_intent_side={intent!r} geçersiz; beklenen 'YES' veya 'NO'",
        )

    # Bridge-intent-only: sadece eşleştirilmiş tarafı değerlendir.
    # spread = ask - bid (derived, dışarıdan gelen snapshot alanına güvenilmez).
    # Edge: execution_realism ile gerçekçi EV (slippage+staleness+fill).
    if intent == "YES":
        spread       = pricing.ask_yes - pricing.bid_yes   # Phase 10: derived
        max_spread   = config.max_spread_yes
        eff_prob     = cal_signal.effective_yes_prob
        execute_type = TradeDecisionType.EXECUTE_YES
        edge = estimate_yes_edge_realistic(
            cal_signal, pricing, config,
            snapshot_age_seconds=snapshot_age,
            intended_size_usdc=_size,
            policy_mode=config.mode,
        )
    else:
        spread       = pricing.ask_no - pricing.bid_no     # Phase 10: derived
        max_spread   = config.max_spread_no
        eff_prob     = cal_signal.effective_no_prob
        execute_type = TradeDecisionType.EXECUTE_NO
        edge = estimate_no_edge_realistic(
            cal_signal, pricing, config,
            snapshot_age_seconds=snapshot_age,
            intended_size_usdc=_size,
            policy_mode=config.mode,
        )

    # Audit alanlarını hesapla — edge mevcutsa steps 10-12 için kullanılabilir.
    # theoretical_hold_ev: p - ask (friction-free upper bound)
    breakdown = edge.executable_cost_breakdown
    _theoretical_hold_ev: float | None = (
        breakdown.theoretical_hold_ev
        if breakdown is not None
        else edge.gross_expected_value
    )
    _net_ev_after_fee: float | None = edge.net_expected_value

    # 10. Spread kontrolü (derived spread — snapshot alanı bypass edildi)
    if spread > max_spread:
        return TradeDecision(
            decision=TradeDecisionType.REJECT,
            rationale=(
                f"REJECT: {intent} spread={spread:.4f} > max={max_spread:.4f} "
                f"(derived: ask-bid)"
            ),
            rejection_reason=CalibrationRejectionReason.HIGH_SPREAD,
            edge_estimate=edge,
            calibrated_signal=cal_signal,
            theoretical_hold_ev=_theoretical_hold_ev,
            net_ev_after_fee=_net_ev_after_fee,
            final_gate_metric="executable_ev",
            final_gate_threshold=config.min_execution_adjusted_edge,
            passes_final_gate=False,
            policy_mode=config.mode,
            intended_size_usdc_used=_size,
        )

    # 11. Minimum kalibre olasılık
    if eff_prob < config.min_calibrated_confidence:
        return TradeDecision(
            decision=TradeDecisionType.REJECT,
            rationale=(
                f"REJECT: {intent} eff_prob={eff_prob:.3f} < "
                f"min_confidence={config.min_calibrated_confidence:.3f}"
            ),
            rejection_reason=CalibrationRejectionReason.LOW_CONFIDENCE,
            edge_estimate=edge,
            calibrated_signal=cal_signal,
            theoretical_hold_ev=_theoretical_hold_ev,
            net_ev_after_fee=_net_ev_after_fee,
            final_gate_metric="executable_ev",
            final_gate_threshold=config.min_execution_adjusted_edge,
            passes_final_gate=False,
            policy_mode=config.mode,
            intended_size_usdc_used=_size,
        )

    # Phase 11: Live strict partial fill rejection (between steps 11 and 12).
    # In live mode, PARTIAL fills are rejected before the edge gate — residual risk is
    # unacceptable for real capital. Paper mode allows PARTIAL with EV penalty applied.
    if (
        config.mode == "live"
        and breakdown is not None
        and breakdown.fill_sim.fill_decision == FillDecision.PARTIAL
    ):
        return TradeDecision(
            decision=TradeDecisionType.REJECT,
            rationale=(
                f"REJECT: live mode partial fill rejected | "
                f"fill_fraction={breakdown.fill_fraction:.2%} | "
                f"intended={_size:.1f} USDC → executable={breakdown.executable_notional_usdc:.1f} USDC | "
                f"policy=live_strict (Option C)"
            ),
            rejection_reason=CalibrationRejectionReason.PARTIAL_FILL_REJECTED,
            edge_estimate=edge,
            calibrated_signal=cal_signal,
            executable_cost_breakdown=breakdown,
            theoretical_hold_ev=_theoretical_hold_ev,
            net_ev_after_fee=_net_ev_after_fee,
            final_gate_metric="executable_ev",
            final_gate_threshold=config.min_execution_adjusted_edge,
            passes_final_gate=False,
            policy_mode=config.mode,
            intended_size_usdc_used=_size,
            executable_notional_usdc=breakdown.executable_notional_usdc,
            fill_fraction=breakdown.fill_fraction,
        )

    # 12. Edge eşiği — executable EV (execution realism dahil)
    if not edge.passes_edge_gate:
        return TradeDecision(
            decision=TradeDecisionType.REJECT,
            rationale=(
                f"REJECT: {intent} executable_ev={edge.expected_edge:.4f} < "
                f"threshold={config.min_execution_adjusted_edge:.3f} | "
                f"exec_adj_ev={edge.execution_adjusted_ev:.4f}"
            ),
            rejection_reason=CalibrationRejectionReason.NEGATIVE_EDGE,
            edge_estimate=edge,
            calibrated_signal=cal_signal,
            theoretical_hold_ev=_theoretical_hold_ev,
            net_ev_after_fee=_net_ev_after_fee,
            final_gate_metric="executable_ev",
            final_gate_threshold=config.min_execution_adjusted_edge,
            passes_final_gate=False,
            policy_mode=config.mode,
            intended_size_usdc_used=_size,
        )

    # Tüm 12 kontrol geçildi → EXECUTE
    staleness_info = (
        f"staleness={breakdown.staleness.zone.value} "
        f"penalty={breakdown.staleness.penalty:.4f} | "
        if breakdown is not None else ""
    )
    rationale = (
        f"{execute_type.value} | "
        f"intent={intent} | "
        f"executable_ev={edge.expected_edge:.4f} | "
        f"exec_adj_ev={edge.execution_adjusted_ev:.4f} | "
        f"p_event={edge.calibrated_event_probability:.3f} | "
        f"ask={edge.market_entry_price:.4f} | "
        f"{staleness_info}"
        f"cal={cal_signal.calibration_quality.value} | "
        f"{cal_signal.mapping_context}"
    )

    return TradeDecision(
        decision=execute_type,
        rationale=rationale,
        rejection_reason=None,
        edge_estimate=edge,
        calibrated_signal=cal_signal,
        executable_cost_breakdown=breakdown,
        theoretical_hold_ev=_theoretical_hold_ev,
        net_ev_after_fee=_net_ev_after_fee,
        final_gate_metric="executable_ev",
        final_gate_threshold=config.min_execution_adjusted_edge,
        passes_final_gate=True,
        policy_mode=config.mode,
        intended_size_usdc_used=_size,
        executable_notional_usdc=breakdown.executable_notional_usdc if breakdown is not None else None,
        fill_fraction=breakdown.fill_fraction if breakdown is not None else None,
        pricing_sanity_notes=_sanity_note,
    )


# ── Yardımcı fonksiyonlar ─────────────────────────────────────────────────────

def _validate_pricing_snapshot(
    pricing: MarketPricingSnapshot,
    now_utc: datetime,
) -> str | None:
    """
    Pricing snapshot'ın yapısal geçerliliğini doğrular.

    Hata varsa açıklayıcı mesaj döndürür; geçerliyse None döndürür.

    Kontroller:
    - Tüm fiyatlar [0, 1] aralığında
    - ask_yes >= bid_yes (ters spread yok)
    - ask_no  >= bid_no
    - Zaman damgası gelecekte değil
    """
    prices = {
        "ask_yes": pricing.ask_yes,
        "bid_yes": pricing.bid_yes,
        "ask_no":  pricing.ask_no,
        "bid_no":  pricing.bid_no,
    }
    for name, val in prices.items():
        if not (0.0 <= val <= 1.0):
            return f"REJECT: {name}={val:.4f} [0,1] dışında"

    if pricing.ask_yes < pricing.bid_yes:
        return (
            f"REJECT: ask_yes={pricing.ask_yes:.4f} < bid_yes={pricing.bid_yes:.4f} "
            f"(ters spread)"
        )
    if pricing.ask_no < pricing.bid_no:
        return (
            f"REJECT: ask_no={pricing.ask_no:.4f} < bid_no={pricing.bid_no:.4f} "
            f"(ters spread)"
        )

    # Hard structural floor: ask_sum below BINARY_HARD_MIN_ASK_SUM is data corruption.
    # Policy-specific suspicious underround is caught separately in _check_binary_sanity.
    ask_sum = pricing.ask_yes + pricing.ask_no
    if ask_sum < BINARY_HARD_MIN_ASK_SUM:
        return (
            f"REJECT: ask_yes({pricing.ask_yes:.4f}) + ask_no({pricing.ask_no:.4f}) = "
            f"{ask_sum:.4f} < hard floor {BINARY_HARD_MIN_ASK_SUM} (structural corruption)"
        )

    # Hard bid_sum ceiling: bid_yes + bid_no > 1.01 is risk-free arb — impossible in real markets.
    bid_sum = pricing.bid_yes + pricing.bid_no
    if bid_sum > BINARY_HARD_MAX_BID_SUM:
        return (
            f"REJECT: bid_yes({pricing.bid_yes:.4f}) + bid_no({pricing.bid_no:.4f}) = "
            f"{bid_sum:.4f} > hard ceiling {BINARY_HARD_MAX_BID_SUM} (risk-free arb impossible)"
        )

    # Gelecek zaman damgası
    ts = pricing.timestamp_utc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    if ts > now_utc:
        drift = (ts - now_utc).total_seconds()
        return f"REJECT: pricing.timestamp_utc gelecekte ({drift:.1f}s ileri)"

    return None


def _snapshot_age_seconds(snapshot_ts: datetime, now_utc: datetime) -> float:
    """Snapshot'ın kaç saniye önce alındığını hesaplar."""
    if snapshot_ts.tzinfo is None:
        snapshot_ts = snapshot_ts.replace(tzinfo=timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    delta = (now_utc - snapshot_ts).total_seconds()
    return max(0.0, delta)


def _check_binary_sanity(
    pricing: MarketPricingSnapshot,
    config: CalibrationConfig,
) -> str | None:
    """
    Phase 12: policy-specific binary market sanity check.

    Returns an error message string if the pricing is suspiciously malformed
    for the given policy profile, None otherwise.

    This runs AFTER _validate_pricing_snapshot (which catches hard structural errors).
    These checks are softer — they catch economically suspicious but not impossible quotes.

    Triggered conditions → SUSPICIOUS_UNDERROUND rejection:
      1. ask_sum out of policy band (too low = underround, too high = excessive vig)
      2. bid_sum > policy max (near-arb territory) [live and paper_strict only]
      3. Individual ask < policy floor (one-sided quote pathology) [live and paper_strict only]

    For paper_loose: same checks, but caller annotates rather than rejects.
    """
    mode = config.mode

    # Resolve effective thresholds (explicit config overrides take priority)
    if mode == "live":
        _min_ask = config.min_ask_sum_binary if config.min_ask_sum_binary is not None else BINARY_SANITY_MIN_ASK_SUM_LIVE
        _max_ask = config.max_ask_sum_binary if config.max_ask_sum_binary is not None else BINARY_SANITY_MAX_ASK_SUM_LIVE
        _max_bid = config.max_bid_sum_binary if config.max_bid_sum_binary is not None else BINARY_SANITY_MAX_BID_SUM_LIVE
        _min_single = config.min_single_ask_binary if config.min_single_ask_binary is not None else BINARY_SANITY_MIN_SINGLE_ASK_LIVE
    elif mode == "paper_strict":
        _min_ask = config.min_ask_sum_binary if config.min_ask_sum_binary is not None else BINARY_SANITY_MIN_ASK_SUM_PAPER_STRICT
        _max_ask = config.max_ask_sum_binary if config.max_ask_sum_binary is not None else BINARY_SANITY_MAX_ASK_SUM_PAPER_STRICT
        _max_bid = config.max_bid_sum_binary if config.max_bid_sum_binary is not None else BINARY_SANITY_MAX_BID_SUM_PAPER_STRICT
        _min_single = config.min_single_ask_binary if config.min_single_ask_binary is not None else BINARY_SANITY_MIN_SINGLE_ASK_PAPER_STRICT
    else:
        # paper_loose / paper / default — use loose thresholds
        _min_ask = config.min_ask_sum_binary if config.min_ask_sum_binary is not None else BINARY_SANITY_MIN_ASK_SUM_PAPER_LOOSE
        _max_ask = config.max_ask_sum_binary if config.max_ask_sum_binary is not None else BINARY_SANITY_MAX_ASK_SUM_PAPER_LOOSE
        _max_bid = None   # paper_loose: bid_sum not checked
        _min_single = None  # paper_loose: no individual check

    ask_sum = pricing.ask_yes + pricing.ask_no
    bid_sum = pricing.bid_yes + pricing.bid_no

    # 1. ask_sum band check
    if ask_sum < _min_ask:
        return (
            f"SUSPICIOUS_UNDERROUND: ask_yes({pricing.ask_yes:.4f}) + "
            f"ask_no({pricing.ask_no:.4f}) = {ask_sum:.4f} < "
            f"min_ask_sum={_min_ask:.2f} [mode={mode}] "
            f"(underround: binary market should cost ≥ {_min_ask:.2f} to buy both sides)"
        )
    if ask_sum > _max_ask:
        return (
            f"SUSPICIOUS_UNDERROUND: ask_sum={ask_sum:.4f} > "
            f"max_ask_sum={_max_ask:.2f} [mode={mode}] "
            f"(excessive vig: {ask_sum - 1.0:.3f} overround above fair)"
        )

    # 2. bid_sum ceiling (live and paper_strict only)
    if config.check_bid_overround and _max_bid is not None and bid_sum > _max_bid:
        return (
            f"SUSPICIOUS_UNDERROUND: bid_yes({pricing.bid_yes:.4f}) + "
            f"bid_no({pricing.bid_no:.4f}) = {bid_sum:.4f} > "
            f"max_bid_sum={_max_bid:.2f} [mode={mode}] "
            f"(near risk-free arb territory)"
        )

    # 3. Individual ask floor (pathological one-sided quote)
    if _min_single is not None:
        for side_name, ask_val in (("ask_yes", pricing.ask_yes), ("ask_no", pricing.ask_no)):
            if ask_val < _min_single:
                return (
                    f"SUSPICIOUS_UNDERROUND: {side_name}={ask_val:.4f} < "
                    f"min_single_ask={_min_single:.2f} [mode={mode}] "
                    f"(one-sided quote pathology: market is near-certain on one side)"
                )

    return None


def _effective_max_age(config: CalibrationConfig, horizon_minutes: int) -> float:
    """
    Etkin maksimum snapshot yaşını hesaplar.

    max_snapshot_age_horizon_fraction None ise sabit eşik döndürür.
    Aksi hâlde: min(max_snapshot_age_seconds, horizon_minutes * 60 * fraksiyon)
    """
    if config.max_snapshot_age_horizon_fraction is None:
        return float(config.max_snapshot_age_seconds)
    horizon_seconds = horizon_minutes * 60
    horizon_derived = horizon_seconds * config.max_snapshot_age_horizon_fraction
    return min(float(config.max_snapshot_age_seconds), horizon_derived)
