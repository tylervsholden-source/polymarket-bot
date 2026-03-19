"""
operator_layer — Architect Chamber backend.

Provides data contracts, ledger readers, PnL computation,
health aggregation, readiness views, and the main aggregator
that powers the Architect Chamber dashboard.

Import paths:
    from operator_layer.types import ChamberSummary, DecisionEvent, ...
    from operator_layer.aggregator import build_chamber_summary
    from operator_layer.api import register_chamber_routes
"""
