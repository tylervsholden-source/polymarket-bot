"""
calibration/tests/test_package_integrity.py

Phase 10: Package self-containment and import graph integrity.

Proves:
1. All required modules can be imported without error
2. No hidden dependency on absent prior-phase files
3. Core decision path is runnable end-to-end from a cold import
4. Config source of truth is Python (not YAML) — no YAML files expected
5. Key public APIs are present and callable
"""
from __future__ import annotations

import importlib
import os
from datetime import datetime, timezone
from pathlib import Path


_REPO_ROOT = Path(__file__).parent.parent.parent  # Polymarket/


# ── 1. Module import graph ─────────────────────────────────────────────────────

class TestModuleImports:
    """Every module in the decision path must import cleanly."""

    REQUIRED_MODULES = [
        "calibration.types",
        "calibration.decision_policy",
        "calibration.edge_estimator",
        "calibration.probability_mapper",
        "execution_realism.core",
        "execution_realism.types",
        "execution_realism.slippage_model",
        "execution_realism.staleness_penalty",
        "execution_realism.liquidity_model",
        "execution_realism.fill_simulator",
        "signal_bridge.types",
    ]

    def test_all_required_modules_importable(self):
        """Kanıt #3: 모든 필수 모듈 임포트 가능 — no missing files."""
        for mod_name in self.REQUIRED_MODULES:
            try:
                importlib.import_module(mod_name)
            except ImportError as e:
                raise AssertionError(
                    f"Required module '{mod_name}' failed to import: {e}"
                ) from e

    def test_calibration_types_exports_key_symbols(self):
        """calibration.types exports the expected public API."""
        import calibration.types as ct
        expected = [
            "RawSignalOutput", "CalibratedSignal", "MarketPricingSnapshot",
            "EdgeEstimate", "TradeDecision", "CalibrationConfig",
            "LIVE_CAL_CONFIG", "PAPER_CAL_CONFIG", "DEFAULT_CAL_CONFIG",
            "SUPPORTED_HORIZONS", "PROB_MIN_SUM", "PROB_LIVE_MIN_SUM",
            "PROB_MAX_OVER", "BINARY_MARKET_MAX_OVERROUND",
            "TradeDecisionType", "CalibrationRejectionReason",
        ]
        for name in expected:
            assert hasattr(ct, name), f"calibration.types missing: {name}"

    def test_decision_policy_exports_decide(self):
        """calibration.decision_policy exports decide()."""
        from calibration.decision_policy import decide
        assert callable(decide)

    def test_execution_realism_exports_compute_executable_ev(self):
        """execution_realism.core exports compute_executable_ev()."""
        from execution_realism.core import compute_executable_ev
        assert callable(compute_executable_ev)

    def test_probability_mapper_exports_map_fn(self):
        from calibration.probability_mapper import map_to_event_probability
        assert callable(map_to_event_probability)


# ── 2. Config source of truth ──────────────────────────────────────────────────

class TestConfigSourceOfTruth:
    """Python constants are the single source of truth — no YAML needed."""

    def test_no_live_yaml_exists(self):
        """config/live.yaml must not exist — Python is authoritative."""
        yaml_path = _REPO_ROOT / "config" / "live.yaml"
        assert not yaml_path.exists(), (
            f"config/live.yaml exists at {yaml_path}. "
            "YAML files are decorative and were removed in Phase 10. "
            "Python constants (LIVE_CAL_CONFIG) are the source of truth."
        )

    def test_no_paper_yaml_exists(self):
        """config/paper.yaml must not exist."""
        yaml_path = _REPO_ROOT / "config" / "paper.yaml"
        assert not yaml_path.exists(), (
            f"config/paper.yaml exists at {yaml_path}. "
            "Python constants (PAPER_CAL_CONFIG) are the source of truth."
        )

    def test_live_config_values_match_spec(self):
        """LIVE_CAL_CONFIG enforces the documented production thresholds."""
        from calibration.types import LIVE_CAL_CONFIG, PROB_LIVE_MIN_SUM
        assert LIVE_CAL_CONFIG.min_execution_adjusted_edge == 0.03
        assert LIVE_CAL_CONFIG.reject_on_unknown_calibration is True
        assert LIVE_CAL_CONFIG.reject_on_weak_calibration is True
        assert LIVE_CAL_CONFIG.require_class_probabilities is True
        assert LIVE_CAL_CONFIG.min_prob_sum == PROB_LIVE_MIN_SUM
        assert LIVE_CAL_CONFIG.mode == "live"

    def test_paper_config_values_match_spec(self):
        """PAPER_CAL_CONFIG is more permissive than live."""
        from calibration.types import PAPER_CAL_CONFIG, PROB_MIN_SUM
        assert PAPER_CAL_CONFIG.reject_on_unknown_calibration is False
        assert PAPER_CAL_CONFIG.reject_on_weak_calibration is False
        assert PAPER_CAL_CONFIG.require_class_probabilities is False
        assert PAPER_CAL_CONFIG.min_prob_sum == PROB_MIN_SUM
        assert PAPER_CAL_CONFIG.mode == "paper"

    def test_loader_py_documents_python_as_source_of_truth(self):
        """config/loader.py must exist and document the source-of-truth policy."""
        loader_path = _REPO_ROOT / "config" / "loader.py"
        assert loader_path.exists(), (
            "config/loader.py must exist to document that Python constants "
            "are the authoritative config source of truth."
        )


# ── 3. End-to-end cold import runnable ────────────────────────────────────────

class TestColdImportRunnable:
    """Core decision path works from a fresh Python import."""

    def test_full_decision_path_runnable(self):
        """
        Kanıt #3: Import → construct → decide — no prior-phase file dependency.
        This test proves the package is self-contained.
        """
        from datetime import timedelta

        from calibration.decision_policy import decide
        from calibration.probability_mapper import map_to_event_probability
        from calibration.types import (
            PAPER_CAL_CONFIG,
            CalibrationMethod,
            CalibrationQuality,
            MarketPricingSnapshot,
            RawSignalOutput,
            TradeDecisionType,
        )

        now = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

        raw = RawSignalOutput(
            asset="BTC",
            horizon_minutes=5,
            timestamp_utc=now,
            predicted_class="UP",
            raw_confidence=0.72,
            class_probabilities={"UP": 0.72, "DOWN": 0.18, "NO_TRADE": 0.10},
        )

        cal, err = map_to_event_probability(
            raw=raw,
            calibrated_up_prob=0.72,
            calibrated_down_prob=0.18,
            calibrated_no_trade_prob=0.10,
            polarity="NORMAL",
            calibration_method=CalibrationMethod.PLATT,
            calibration_quality=CalibrationQuality.STRONG,
        )
        assert err is None, f"Mapper failed: {err}"

        pricing = MarketPricingSnapshot(
            market_id="test-market",
            ask_yes=0.44, bid_yes=0.42,
            ask_no=0.57,  bid_no=0.55,
            liquidity=5000.0,
            timestamp_utc=now,
        )

        result = decide(cal, pricing, config=PAPER_CAL_CONFIG, now_utc=now)
        assert result.decision in (
            TradeDecisionType.EXECUTE_YES,
            TradeDecisionType.EXECUTE_NO,
            TradeDecisionType.REJECT,
        )
        # Audit fields populated
        assert result.policy_mode == "paper"
        assert result.final_gate_metric == "executable_ev"
        assert result.intended_size_usdc_used == 20.0
