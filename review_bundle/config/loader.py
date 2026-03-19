"""
config/loader.py

CONFIG SOURCE OF TRUTH: PYTHON CONSTANTS
=========================================

This project uses Python dataclass instances as the single, authoritative
source of truth for all calibration and execution parameters.

YAML files (config/live.yaml, config/paper.yaml) were removed in Phase 10
because they were decorative — code never read them, and editing them had
zero effect on actual behavior. Leaving them in place was a split-brain risk.

ACTIVE CONFIGURATIONS
---------------------

Import directly from calibration.types:

    from calibration.types import LIVE_CAL_CONFIG    # production
    from calibration.types import PAPER_CAL_CONFIG   # simulation / research
    from calibration.types import DEFAULT_CAL_CONFIG  # minimal defaults

    # Or build a custom config:
    from calibration.types import CalibrationConfig
    custom = CalibrationConfig(
        mode="live",
        min_execution_adjusted_edge=0.04,
        min_prob_sum=0.90,
        ...
    )

WHY NOT YAML
------------
1. Python constants are validated at import time (__post_init__).
2. Edits to Python constants are tracked in git diff.
3. No deserialization layer means no type coercion bugs.
4. Tests that reference LIVE_CAL_CONFIG are directly testing the active config.

HOW TO CHANGE THRESHOLDS
-------------------------
Edit calibration/types.py. The constants LIVE_CAL_CONFIG, PAPER_CAL_CONFIG,
and DEFAULT_CAL_CONFIG are at the bottom of the file.

After editing:
  1. Run: python -m pytest calibration/tests/ execution_realism/tests/
  2. All tests must pass before deploying.
  3. Commit the change — the diff is the audit trail.

CURRENT THRESHOLD SUMMARY (as of Phase 10)
-------------------------------------------

                          LIVE        PAPER       DEFAULT
min_exec_adjusted_edge    0.030       0.020       0.020
min_calibrated_confidence 0.550       0.550       0.550
min_prob_sum              0.900       0.500       0.500
reject_on_unknown_cal     True        False       False
reject_on_weak_cal        True        False       False
require_class_probs       True        False       False
mode                      "live"      "paper"     "default"
"""
# This file is intentionally a documentation module.
# It has no runtime behavior — import calibration.types directly.
