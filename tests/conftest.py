"""Shared pytest fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _pin_et_hour_gate(monkeypatch):
    """Pin ArbitrageEngine's bad-hour gate to a neutral hour so tests are
    deterministic regardless of the real wall-clock time they run at.
    Without this, tests that reach GATE 3 in _evaluate_market randomly
    fail whenever the suite happens to run during 21:00-00:59 ET.
    """
    try:
        import strategies.arbitrage_engine as ae
        monkeypatch.setattr(ae, "_get_current_et_hour", lambda: 12)
    except ImportError:
        pass
