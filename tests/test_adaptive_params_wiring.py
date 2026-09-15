"""
Regression test: AutonomousDecisionEngine.get_adaptive_params()'ın
min_edge_yes/min_edge_no/max_bet_multiplier alanlarının gerçek canlı yola
bağlı olması.

Bug: agents/autonomous_engine.py:get_adaptive_params() docstring'i
"Orchestrator bu değerleri okuyup uygulayabilir" diyordu, ama
agents/orchestrator.py'deki tek çağrı noktası (Orchestrator.run()) sözlükten
sadece cycle_interval_seconds ve aggression alanlarını okuyordu. DEFENSIVE
(win_rate<%40) veya SURVIVAL (capital<$10) moduna geçildiğinde hesaplanan
daha sıkı min_edge/daha küçük bet_size hiçbir zaman gerçek edge gate'ine
(strategies/arbitrage_engine.py'nin min_edge_yes/min_edge_no'su, sabit
0.12/0.18 olarak set edilip hiç değişmiyordu) veya bet sizing'e
(compute_bet_size sonrası uygulanan çarpanlar) ulaşmıyordu — MIN_MARKET_VOLUME/
OPT-2/OPT-3/OPT-6/dashboard-min_bet/REDUCE-double-apply hatalarıyla aynı
"kontrol var ama canlı yola bağlı değil" deseni.

Bu test, run()'ın adaptive sözlüğü her iki alanı (arb_engine.min_edge_yes/no
ve self._adaptive_bet_multiplier) güncellemek için okuduğunu ve _cycle()'ın
bu çarpanı gerçek bet_size'a uyguladığını kilitler. min_edge'in asla statik
tabanın (0.12/0.18) altına düşürülmediği de ayrıca doğrulanır (NORMAL/
AGGRESSIVE modun varsayılanları 0.08/0.15 daha düşük — bunlar sabit tabanı
gevşetmemeli, sadece DEFENSIVE/SURVIVAL sıkılaştırmalı).
"""
from __future__ import annotations

import inspect

from agents.autonomous_engine import AutonomousDecisionEngine
from agents.orchestrator import Orchestrator


def test_run_wires_adaptive_min_edge_and_bet_multiplier():
    src = inspect.getsource(Orchestrator.run)
    assert "self.arb_engine.min_edge_yes = max(self._base_min_edge_yes" in src, (
        "run() must apply adaptive min_edge_yes to arb_engine, floored at the "
        "static base (never loosen below it)"
    )
    assert "self.arb_engine.min_edge_no = max(self._base_min_edge_no" in src, (
        "run() must apply adaptive min_edge_no to arb_engine, floored at the "
        "static base (never loosen below it)"
    )
    assert "self._adaptive_bet_multiplier = adaptive[\"max_bet_multiplier\"]" in src, (
        "run() must store max_bet_multiplier for _cycle() to apply to real bet_size"
    )


def test_cycle_applies_adaptive_bet_multiplier_to_bet_size():
    # 45th daily review: _cycle() used to do a bare `bet_size *=
    # self._adaptive_bet_multiplier`, which (for AGGRESSIVE mode's 1.15x)
    # could push bet_size back above CLAUDE.md's 20%-of-capital position cap
    # that compute_bet_size() had just enforced — see
    # tests/test_adaptive_bet_multiplier_position_cap.py. The multiplier is
    # now applied via apply_adaptive_bet_multiplier(), which re-clamps to
    # that same cap; assert _cycle() routes through it rather than
    # multiplying bet_size directly (or just logging/discarding it).
    src = inspect.getsource(Orchestrator._cycle)
    assert "bet_size = apply_adaptive_bet_multiplier(" in src, (
        "_cycle() must apply the adaptive engine's max_bet_multiplier to the "
        "real bet_size via apply_adaptive_bet_multiplier() (re-clamped to the "
        "20% position cap), not just log/discard it or multiply it unclamped"
    )
    assert "bet_size *= self._adaptive_bet_multiplier" not in src, (
        "a bare multiply here would bypass apply_adaptive_bet_multiplier()'s "
        "20%-of-capital position-cap re-clamp"
    )


def test_init_captures_base_edge_floor_before_any_adaptation():
    src = inspect.getsource(Orchestrator.__init__)
    assert "self._base_min_edge_yes = self.arb_engine.min_edge_yes" in src
    assert "self._base_min_edge_no = self.arb_engine.min_edge_no" in src
    assert "self._adaptive_bet_multiplier = 1.0" in src


def test_defensive_params_never_loosen_below_static_base():
    # DEFENSIVE (win_rate<0.40) suggests min_edge_yes=0.10, which is *below*
    # the static base of 0.12 — max(base, adaptive) must keep the stricter
    # static floor, not accidentally loosen it.
    engine = AutonomousDecisionEngine()
    engine._performance.win_rate = 0.30
    engine._performance.total_trades = 20
    engine._performance.capital = 100.0  # above LOW_CAPITAL_THRESHOLD, isolates DEFENSIVE
    params = engine.get_adaptive_params()
    assert params["min_edge_yes"] == 0.10

    base_min_edge_yes = 0.12
    applied = max(base_min_edge_yes, params["min_edge_yes"])
    assert applied == base_min_edge_yes, (
        "DEFENSIVE mode's min_edge_yes suggestion is below the static base — "
        "applying it directly would loosen the live edge gate, not tighten it"
    )


def test_survival_params_tighten_above_static_base():
    engine = AutonomousDecisionEngine()
    engine._performance.capital = 5.0
    params = engine.get_adaptive_params()
    assert params["min_edge_no"] == 0.25
    assert params["max_bet_multiplier"] == 0.3

    base_min_edge_no = 0.18
    applied = max(base_min_edge_no, params["min_edge_no"])
    assert applied == 0.25, "SURVIVAL mode should tighten min_edge_no above the static base"


def test_consecutive_losses_do_not_overwrite_defensive_aggression_label():
    # Bug: a losing streak (>=3) unconditionally stamped aggression="AGGRESSIVE"
    # even when win_rate<0.40 had already set it to "DEFENSIVE" (tighter
    # min_edge/max_bet_multiplier still applied — only the label was wrong).
    # "aggression" is log-only (grep confirms no code branches on its value
    # besides logger.info calls in orchestrator.run()), but daily strategy
    # reviews read exactly that log line to diagnose bot state, so a wrong
    # label there is a real (if non-monetary) correctness bug.
    engine = AutonomousDecisionEngine()
    engine._performance.win_rate = 0.30
    engine._performance.total_trades = 20
    engine._performance.capital = 100.0
    engine._performance.consecutive_losses = 5
    params = engine.get_adaptive_params()

    assert params["aggression"] == "DEFENSIVE", (
        "a losing streak must not overwrite the DEFENSIVE label set by a low "
        "win rate — the tighter min_edge/max_bet_multiplier are still in "
        "effect and the log should say so"
    )
    assert params["cycle_interval_seconds"] == 60


def test_consecutive_losses_still_label_aggression_when_otherwise_normal():
    engine = AutonomousDecisionEngine()
    engine._performance.capital = 100.0  # above LOW_CAPITAL_THRESHOLD, isolates this branch
    engine._performance.consecutive_losses = 3
    params = engine.get_adaptive_params()
    assert params["aggression"] == "AGGRESSIVE"
    assert params["cycle_interval_seconds"] == 60
