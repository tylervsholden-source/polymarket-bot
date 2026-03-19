"""
monitoring/alerts.py

Phase 13: Alert rules and triggering for drift reports.

Design decisions:
  1. Alert thresholds are RELATIVE changes (percentages), not absolute values.
     Rationale: absolute rejection rates vary by market conditions. A 20pp
     absolute change in an already-80%-reject regime is less alarming than
     a 20pp change starting from 10%. Relative change is more meaningful.
  2. Two alert levels:
       WARNING  — drift is notable, warrants attention
       CRITICAL — drift is severe, warrants immediate investigation
  3. All alert rules are configurable via AlertConfig. Defaults are opinionated
     but can be overridden per deployment.
  4. AlertEngine is stateless — it checks a DriftReport and returns alerts.
     No state stored between calls. History / deduplication is the caller's job.
  5. Alert triggers:
       a. rejection_rate_delta > WARN threshold (absolute percentage points)
       b. rejection_rate_delta > CRIT threshold
       c. ev_mean_delta < -WARN threshold (EV dropped by X)
       d. ev_mean_delta < -CRIT threshold
       e. divergence_rate > WARN threshold
       f. divergence_rate > CRIT threshold

Thresholds (defaults):
  Rejection rate delta:
    WARNING:  ±0.15  (15pp change)
    CRITICAL: ±0.30  (30pp change)
  EV mean delta:
    WARNING:  -0.005  (EV dropped by 0.5pp)
    CRITICAL: -0.015  (EV dropped by 1.5pp)
  Divergence rate:
    WARNING:  0.20   (20% of candidates disagree between profiles)
    CRITICAL: 0.40   (40% divergence)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from shadow_runner.types import DriftAlert, DriftReport


@dataclass
class AlertConfig:
    """Configurable thresholds for alert triggers."""
    # Rejection rate absolute delta (current - baseline)
    rejection_rate_warn:   float = 0.15
    rejection_rate_crit:   float = 0.30
    # EV mean delta (negative = EV dropped)
    ev_mean_drop_warn:     float = 0.005
    ev_mean_drop_crit:     float = 0.015
    # Profile divergence rate
    divergence_warn:       float = 0.20
    divergence_crit:       float = 0.40
    # Minimum window size to trigger alerts.
    # Alerts are suppressed if current_window_size < this.
    min_window_size:       int   = 10


DEFAULT_ALERT_CONFIG = AlertConfig()


class AlertEngine:
    """
    Evaluates a DriftReport against alert thresholds.

    Usage
    -----
        engine = AlertEngine()
        alerts = engine.check(drift_report)
        for alert in alerts:
            print(alert.level, alert.message)
    """

    def __init__(self, config: AlertConfig = DEFAULT_ALERT_CONFIG) -> None:
        self._cfg = config

    def check(self, report: DriftReport) -> list[DriftAlert]:
        """
        Evaluate a DriftReport and return triggered alerts.

        Returns an empty list if no thresholds are exceeded or if the
        current window is too small to be meaningful.
        """
        alerts: list[DriftAlert] = []

        if report.current_window_size < self._cfg.min_window_size:
            return alerts  # too small to judge

        # 1. Rejection rate drift per profile
        for profile in report.profiles_checked:
            delta = report.rejection_rate_delta.get(profile, 0.0)
            base_rate = report.baseline_rejection[profile].rejection_rate
            cur_rate  = report.current_rejection[profile].rejection_rate

            abs_delta = abs(delta)
            if abs_delta >= self._cfg.rejection_rate_crit:
                alerts.append(DriftAlert(
                    level="CRITICAL",
                    metric="rejection_rate",
                    policy_profile=profile,
                    message=(
                        f"[{profile}] rejection rate CRITICAL drift: "
                        f"{base_rate:.1%} → {cur_rate:.1%} "
                        f"(Δ={delta:+.1%})"
                    ),
                    baseline_value=base_rate,
                    current_value=cur_rate,
                    delta=delta,
                    threshold=self._cfg.rejection_rate_crit,
                ))
            elif abs_delta >= self._cfg.rejection_rate_warn:
                alerts.append(DriftAlert(
                    level="WARNING",
                    metric="rejection_rate",
                    policy_profile=profile,
                    message=(
                        f"[{profile}] rejection rate WARNING drift: "
                        f"{base_rate:.1%} → {cur_rate:.1%} "
                        f"(Δ={delta:+.1%})"
                    ),
                    baseline_value=base_rate,
                    current_value=cur_rate,
                    delta=delta,
                    threshold=self._cfg.rejection_rate_warn,
                ))

        # 2. EV mean drift per profile (only if both baseline and current have executes)
        for profile in report.profiles_checked:
            delta = report.ev_mean_delta.get(profile, 0.0)
            if delta == 0.0:
                continue  # no data or no change
            base_ev_m = report.baseline_ev.get(profile)
            cur_ev_m  = report.current_ev.get(profile)
            if base_ev_m is None or cur_ev_m is None:
                continue

            base_val = base_ev_m.mean_exec_adj_ev
            cur_val  = cur_ev_m.mean_exec_adj_ev

            if delta <= -self._cfg.ev_mean_drop_crit:
                alerts.append(DriftAlert(
                    level="CRITICAL",
                    metric="ev_mean",
                    policy_profile=profile,
                    message=(
                        f"[{profile}] EV mean CRITICAL drop: "
                        f"{base_val:.4f} → {cur_val:.4f} "
                        f"(Δ={delta:+.4f})"
                    ),
                    baseline_value=base_val,
                    current_value=cur_val,
                    delta=delta,
                    threshold=-self._cfg.ev_mean_drop_crit,
                ))
            elif delta <= -self._cfg.ev_mean_drop_warn:
                alerts.append(DriftAlert(
                    level="WARNING",
                    metric="ev_mean",
                    policy_profile=profile,
                    message=(
                        f"[{profile}] EV mean WARNING drop: "
                        f"{base_val:.4f} → {cur_val:.4f} "
                        f"(Δ={delta:+.4f})"
                    ),
                    baseline_value=base_val,
                    current_value=cur_val,
                    delta=delta,
                    threshold=-self._cfg.ev_mean_drop_warn,
                ))

        # 3. Profile divergence
        for div in report.divergence:
            rate = div.divergence_rate
            pair_label = f"{div.profile_a}/{div.profile_b}"
            if rate >= self._cfg.divergence_crit:
                alerts.append(DriftAlert(
                    level="CRITICAL",
                    metric="divergence_rate",
                    policy_profile=pair_label,
                    message=(
                        f"[{pair_label}] profile divergence CRITICAL: "
                        f"{rate:.1%} of {div.total_shared} shared candidates disagree"
                    ),
                    baseline_value=0.0,
                    current_value=rate,
                    delta=rate,
                    threshold=self._cfg.divergence_crit,
                ))
            elif rate >= self._cfg.divergence_warn:
                alerts.append(DriftAlert(
                    level="WARNING",
                    metric="divergence_rate",
                    policy_profile=pair_label,
                    message=(
                        f"[{pair_label}] profile divergence WARNING: "
                        f"{rate:.1%} of {div.total_shared} shared candidates disagree"
                    ),
                    baseline_value=0.0,
                    current_value=rate,
                    delta=rate,
                    threshold=self._cfg.divergence_warn,
                ))

        return alerts
