"""
Walk-Forward Validation: Compare backtest vs live performance.

Instead of standard backtesting (train on all data → test on same data = overfitting),
walk-forward splits into rolling windows:
  Train: last N trades → calculate expected WR + edge
  Test: next M trades → compare actual WR + edge
  If gap > threshold → model is stale, reduce position sizes

Usage:
    validator = WalkForwardValidator()
    validator.update(closed_trades)
    is_valid, gap = validator.check()
"""

from loguru import logger
from typing import Optional


class WalkForwardValidator:
    def __init__(self, train_window: int = 50, test_window: int = 20, max_gap: float = 0.15):
        self.train_window = train_window
        self.test_window = test_window
        self.max_gap = max_gap  # max allowable gap between train WR and test WR
        self._last_check: dict = {}

    def validate(self, closed_trades: list[dict]) -> dict:
        """Run walk-forward validation on closed trades.

        Returns:
            {
                "is_valid": bool,        # True if model is still performing as expected
                "train_wr": float,       # Win rate in training window
                "test_wr": float,        # Win rate in test window
                "gap": float,            # Absolute difference
                "train_avg_edge": float, # Average edge in training window
                "test_avg_edge": float,  # Average edge in test window
                "recommendation": str,   # "FULL_SIZE" / "HALF_SIZE" / "STOP"
                "confidence_multiplier": float,  # 0.0 to 1.0 — multiply Kelly by this
            }
        """
        total_needed = self.train_window + self.test_window
        if len(closed_trades) < total_needed:
            return {
                "is_valid": True,
                "train_wr": 0.0,
                "test_wr": 0.0,
                "gap": 0.0,
                "train_avg_edge": 0.0,
                "test_avg_edge": 0.0,
                "recommendation": "FULL_SIZE",
                "confidence_multiplier": 1.0,
            }

        # Split: train = older trades, test = most recent trades
        test_trades = closed_trades[-self.test_window:]
        train_trades = closed_trades[-(total_needed):-self.test_window]

        # Calculate win rates — NEUTRAL (unfilled/cancelled GTC order, USDC
        # refunded, pnl=0 — see position_manager._close_position_neutral())
        # is neither a win nor a loss. Classifying by raw pnl>0 counted every
        # NEUTRAL close as a LOSS in the denominator, diluting train/test WR
        # for a routine execution outcome rather than a real losing trade.
        # Same bug class already fixed in agents/autonomous_engine.py (#44)
        # and agents/trade_analyzer.py (#46); walk_forward.py was missed.
        train_wins = sum(1 for t in train_trades if t.get("result") == "WIN")
        train_losses = sum(1 for t in train_trades if t.get("result") == "LOSS")
        test_wins = sum(1 for t in test_trades if t.get("result") == "WIN")
        test_losses = sum(1 for t in test_trades if t.get("result") == "LOSS")

        train_wr = train_wins / max(train_wins + train_losses, 1)
        test_wr = test_wins / max(test_wins + test_losses, 1)

        # Calculate average edge (PnL as proxy)
        train_avg_pnl = sum(t.get("pnl", 0) for t in train_trades) / len(train_trades) if train_trades else 0
        test_avg_pnl = sum(t.get("pnl", 0) for t in test_trades) / len(test_trades) if test_trades else 0

        gap = abs(train_wr - test_wr)

        # Determine recommendation
        # FIX: Old thresholds created death spiral — STOP at 35% WR with 0.25x
        # made bets too small → forced to $3 floor → random sizing → worse WR → repeat.
        # New: only STOP at pure noise level (20%), softer multipliers elsewhere.
        if test_wr < 0.20:  # Pure noise — model is completely broken
            recommendation = "STOP"
            confidence_multiplier = 0.40
        elif test_wr < 0.35:  # Bad but recoverable
            recommendation = "HALF_SIZE"
            confidence_multiplier = 0.60
        elif gap > self.max_gap:  # Model is stale
            recommendation = "HALF_SIZE"
            confidence_multiplier = 0.70
        elif test_wr < 0.45:  # Below breakeven
            recommendation = "HALF_SIZE"
            confidence_multiplier = 0.80
        else:
            recommendation = "FULL_SIZE"
            confidence_multiplier = 1.0

        is_valid = recommendation == "FULL_SIZE"

        result = {
            "is_valid": is_valid,
            "train_wr": round(train_wr, 3),
            "test_wr": round(test_wr, 3),
            "gap": round(gap, 3),
            "train_avg_edge": round(train_avg_pnl, 4),
            "test_avg_edge": round(test_avg_pnl, 4),
            "recommendation": recommendation,
            "confidence_multiplier": confidence_multiplier,
        }

        self._last_check = result

        logger.info(
            f"WALK_FORWARD: train_WR={train_wr:.1%} test_WR={test_wr:.1%} "
            f"gap={gap:.1%} → {recommendation} (mult={confidence_multiplier:.2f})"
        )

        return result

    @property
    def last_check(self) -> dict:
        return self._last_check
