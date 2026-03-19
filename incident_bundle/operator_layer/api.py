"""
operator_layer/api.py

Architect Chamber API endpoint handlers.

These functions are called by core/web_server.py when routes under
/api/chamber/* are hit.

Each function returns JSON-serializable bytes.

Routes:
  GET /api/chamber/summary          → full ChamberSummary payload
  GET /api/chamber/decisions        → recent decisions feed
  GET /api/chamber/positions        → open positions
  GET /api/chamber/trades           → closed trades
  GET /api/chamber/health           → health state
  GET /api/chamber/readiness        → readiness state
  GET /api/chamber/profile-comparison → profile comparison
  GET /api/chamber/decision/<id>    → single decision drilldown chain
  GET /api/chamber/equity           → equity / capital state

Serialization notes:
  - datetime → ISO-8601 string with "Z" suffix
  - dataclasses → dict via _to_dict()
  - None → null
  - All responses include a "generated_utc" field for cache-busting
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from typing import Any

from operator_layer.aggregator import build_chamber_summary, get_decision_chain


# ── Serialization ──────────────────────────────────────────────────────────────

def _default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%dT%H:%M:%SZ")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"Not serializable: {type(obj)}")


def _to_json(obj: Any) -> bytes:
    return json.dumps(obj, default=_default, ensure_ascii=False).encode("utf-8")


# ── Cached summary ─────────────────────────────────────────────────────────────
# Build once per request — no in-process caching to avoid stale data.

def handle_summary() -> bytes:
    """
    GET /api/chamber/summary

    Full ChamberSummary — the primary dashboard payload.
    Frontend polls this every 5 seconds.
    """
    summary = build_chamber_summary(max_decisions=50)
    return _to_json(summary)


def handle_decisions(limit: int = 50) -> bytes:
    """
    GET /api/chamber/decisions[?limit=N]

    Recent decision events, most recent first.
    """
    summary = build_chamber_summary(max_decisions=limit)
    return _to_json({
        "decisions": summary.recent_decisions,
        "total_records": summary.total_shadow_records,
        "generated_utc": summary.generated_utc,
    })


def handle_positions() -> bytes:
    """
    GET /api/chamber/positions

    Open positions view.
    """
    summary = build_chamber_summary(max_decisions=0)
    return _to_json({
        "positions":    summary.open_positions,
        "equity":       summary.equity,
        "generated_utc": summary.generated_utc,
    })


def handle_trades(limit: int = 100) -> bytes:
    """
    GET /api/chamber/trades

    Closed trades, most recent first.
    """
    summary = build_chamber_summary(max_decisions=0)
    return _to_json({
        "trades":       summary.closed_trades[:limit],
        "equity":       summary.equity,
        "generated_utc": summary.generated_utc,
    })


def handle_health() -> bytes:
    """
    GET /api/chamber/health

    Health and alert state.
    """
    summary = build_chamber_summary(max_decisions=0)
    return _to_json({
        "health":       summary.health,
        "generated_utc": summary.generated_utc,
    })


def handle_readiness() -> bytes:
    """
    GET /api/chamber/readiness

    Readiness verdict and evidence sufficiency.
    """
    summary = build_chamber_summary(max_decisions=0)
    return _to_json({
        "readiness":    summary.readiness,
        "generated_utc": summary.generated_utc,
    })


def handle_profile_comparison() -> bytes:
    """
    GET /api/chamber/profile-comparison

    Cross-profile comparison summary.
    """
    summary = build_chamber_summary(max_decisions=0)
    return _to_json({
        "comparison":   summary.profile_comparison,
        "generated_utc": summary.generated_utc,
    })


def handle_equity() -> bytes:
    """
    GET /api/chamber/equity

    Equity / capital state only.
    """
    summary = build_chamber_summary(max_decisions=0)
    return _to_json({
        "equity":       summary.equity,
        "generated_utc": summary.generated_utc,
    })


def handle_decision_chain(record_id: str) -> bytes:
    """
    GET /api/chamber/decision/<record_id>

    Full decision chain for drilldown.
    """
    chain = get_decision_chain(record_id)
    if chain is None:
        return json.dumps({"error": f"Record not found: {record_id}"}).encode("utf-8")
    return _to_json(chain)
