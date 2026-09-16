"""
Live Gate — 11-nokta güvenlik kontrolü.

INC-2026-03-15-001 sonrası: Canlı emir vermeden önce TÜM kontrollerin
geçmesi zorunlu. Tek bir fail = emir engellenir.

11 kontrol:
1. process_lock    — Tek instance garantisi
2. live_trading    — control.json live_trading=true
3. readiness       — readiness_verdict.json taze & TINY_PILOT_CANDIDATE
4. daily_stop      — Günlük kayıp limiti aşılmadı
5. position_count  — Max açık pozisyon limiti
6. rate_limit      — Saatlik emir limiti
7. reentry_guard   — Market cooldown kontrolü
8. expiry_guard    — Market süresi dolmamış
9. entry_window    — Start-time-based entry window kontrolü
10. approval       — Emir onay kuyruğunda onaylanmış
11. capital        — Yeterli sermaye
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from loguru import logger

from control_plane.types import LiveGateCheck, LiveGateResult


def check_live_gate(
    *,
    process_lock=None,
    control_file: str = "data/control.json",
    readiness_file: str = "data/readiness_verdict.json",
    readiness_max_age_hours: float = 26.0,
    daily_loss_exceeded: bool = False,
    open_position_count: int = 0,
    max_open_positions: int = 5,
    order_timestamps: list[float] | None = None,
    max_orders_per_hour: int = 3,
    market_id: str = "",
    reentry_guard=None,
    market: dict | None = None,
    expiry_guard=None,
    is_approved: bool = False,
    available_capital: float = 0.0,
    required_capital: float = 0.0,
    # Entry window (PART 3A)
    market_question: str = "",
    entry_window_policy=None,
    is_recheck_after_approval: bool = False,
) -> LiveGateResult:
    """11-nokta güvenlik kontrolü.

    Her parametre opsiyonel — None/default geçilirse o kontrol atlanır (pass).
    Orchestrator tüm parametreleri doldurarak tam kontrol sağlar.

    Returns:
        LiveGateResult — passed=True ise emir verilebilir.
    """
    checks: list[LiveGateCheck] = []

    # 1. Process Lock
    if process_lock is not None:
        held = process_lock.is_mine()
        checks.append(LiveGateCheck(
            name="process_lock",
            passed=held,
            reason="" if held else "Lock bu process'e ait değil",
        ))
    else:
        checks.append(LiveGateCheck(name="process_lock", passed=True, reason="Kontrol atlandı"))

    # 2. Live Trading Enabled
    live = _check_live_trading(control_file)
    checks.append(LiveGateCheck(
        name="live_trading",
        passed=live,
        reason="" if live else "control.json: live_trading=false",
    ))

    # 3. Readiness Verdict
    readiness_ok, readiness_reason = _check_readiness(readiness_file, readiness_max_age_hours)
    checks.append(LiveGateCheck(
        name="readiness",
        passed=readiness_ok,
        reason=readiness_reason,
    ))

    # 4. Daily Stop-Loss
    checks.append(LiveGateCheck(
        name="daily_stop",
        passed=not daily_loss_exceeded,
        reason="" if not daily_loss_exceeded else "Günlük stop-loss limiti aşıldı",
    ))

    # 5. Position Count
    pos_ok = open_position_count < max_open_positions
    checks.append(LiveGateCheck(
        name="position_count",
        passed=pos_ok,
        reason="" if pos_ok else f"Max pozisyon limiti: {open_position_count}/{max_open_positions}",
    ))

    # 6. Rate Limit
    rate_ok = _check_rate_limit(order_timestamps or [], max_orders_per_hour)
    checks.append(LiveGateCheck(
        name="rate_limit",
        passed=rate_ok,
        reason="" if rate_ok else f"Saatlik emir limiti aşıldı (max {max_orders_per_hour})",
    ))

    # 7. Reentry Guard
    if reentry_guard is not None and market_id:
        blocked = reentry_guard.is_blocked(market_id)
        checks.append(LiveGateCheck(
            name="reentry_guard",
            passed=not blocked,
            reason="" if not blocked else f"Market cooldown'da: {market_id[:16]}...",
        ))
    else:
        checks.append(LiveGateCheck(name="reentry_guard", passed=True, reason="Kontrol atlandı"))

    # 8. Expiry Guard
    if expiry_guard is not None and market is not None:
        rej = expiry_guard.check(market)
        checks.append(LiveGateCheck(
            name="expiry_guard",
            passed=rej is None,
            reason="" if rej is None else f"{rej.reason} (h={rej.hours_to_close})",
        ))
    else:
        checks.append(LiveGateCheck(name="expiry_guard", passed=True, reason="Kontrol atlandı"))

    # 9. Entry Window (start-time-based)
    if market_question and entry_window_policy is not None:
        from control_plane.entry_window_guard import check_entry_window
        ew_result = check_entry_window(
            question=market_question,
            policy=entry_window_policy,
            is_recheck_after_approval=is_recheck_after_approval,
        )
        checks.append(LiveGateCheck(
            name="entry_window",
            passed=ew_result.passed,
            reason="" if ew_result.passed else f"{ew_result.rejection.value}: {ew_result.reason}" if ew_result.rejection else ew_result.reason,
        ))
    else:
        checks.append(LiveGateCheck(name="entry_window", passed=True, reason="Kontrol atlandı"))

    # 10. Approval
    checks.append(LiveGateCheck(
        name="approval",
        passed=is_approved,
        reason="" if is_approved else "Emir henüz onaylanmadı",
    ))

    # 11. Capital
    if required_capital > 0:
        cap_ok = available_capital >= required_capital
        checks.append(LiveGateCheck(
            name="capital",
            passed=cap_ok,
            reason="" if cap_ok else f"Yetersiz sermaye: ${available_capital:.2f} < ${required_capital:.2f}",
        ))
    else:
        checks.append(LiveGateCheck(name="capital", passed=True, reason="Kontrol atlandı"))

    all_passed = all(c.passed for c in checks)
    result = LiveGateResult(passed=all_passed, checks=checks)

    if not all_passed:
        blockers = result.blockers
        logger.warning(f"LiveGate ENGEL: {', '.join(blockers)}")

    return result


def _check_live_trading(control_file: str) -> bool:
    """control.json'dan live_trading bayrağı oku."""
    try:
        with open(control_file) as f:
            data = json.load(f)
        return bool(data.get("live_trading", False))
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def _check_readiness(verdict_file: str, max_age_hours: float) -> tuple[bool, str]:
    """readiness_verdict.json kontrolü. (passed, reason) döner."""
    try:
        with open(verdict_file) as f:
            data = json.load(f)
    except FileNotFoundError:
        return False, "readiness_verdict.json bulunamadı"
    except json.JSONDecodeError:
        return False, "readiness_verdict.json okunamadı"

    verdict = data.get("verdict", "")
    if verdict != "TINY_PILOT_CANDIDATE":
        return False, f"verdict={verdict!r} (TINY_PILOT_CANDIDATE gerekli)"

    generated_str = data.get("generated_utc", "")
    if not generated_str:
        return False, "generated_utc eksik — verdict yaşı doğrulanamıyor"

    try:
        generated = datetime.fromisoformat(
            str(generated_str).replace("Z", "+00:00")
        )
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - generated
        if age > timedelta(hours=max_age_hours):
            hours_old = age.total_seconds() / 3600
            return False, f"verdict {hours_old:.1f}h eski (max {max_age_hours}h)"
    except Exception:
        return False, "generated_utc parse hatası"

    return True, ""


def _check_rate_limit(order_timestamps: list[float], max_per_hour: int) -> bool:
    """Son 1 saatteki emir sayısı limiti aşmadı mı?"""
    import time
    cutoff = time.time() - 3600
    recent = [t for t in order_timestamps if t > cutoff]
    return len(recent) < max_per_hour
