"""
operator_layer/ledgers.py

Raw data readers for all operator data sources.

Each reader returns Python-native structures (dicts / lists / dataclasses).
No business logic here — only I/O and parsing.

DATA SOURCES:
  read_positions_ledger()  → positions.json
  read_status_snapshot()   → status.json
  read_control_state()     → control.json
  read_readiness_verdict() → data/readiness_verdict.json
  find_journal_files()     → data/shadow_journal_*.jsonl
  read_journal_records()   → one or more .jsonl files → list[ShadowDecisionRecord]

Design decisions:
  1. All readers return safe defaults (empty dicts / lists) on FileNotFoundError.
     Callers must not assume data is present.
  2. JSON parse errors are caught and logged; the reader returns the safe default
     so a corrupt file does not crash the dashboard.
  3. Journal reading is batched — caller specifies max_records to avoid reading
     unbounded files. Most-recent-first ordering is applied at call time.
  4. Datetime parsing always produces timezone-aware UTC objects.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

# ── Path helpers ──────────────────────────────────────────────────────────────

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_BASE, "data")

POSITIONS_FILE       = os.path.join(_DATA, "positions.json")
STATUS_FILE          = os.path.join(_DATA, "status.json")
CONTROL_FILE         = os.path.join(_DATA, "control.json")
READINESS_FILE       = os.path.join(_DATA, "readiness_verdict.json")
JOURNAL_GLOB_PATTERN = os.path.join(_DATA, "shadow_journal_*.jsonl")


# ── Generic helpers ───────────────────────────────────────────────────────────

def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error in {path}: {e}")
        return {}


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 string to UTC-aware datetime. Returns None on failure."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


# ── Positions ledger ──────────────────────────────────────────────────────────

def read_positions_ledger() -> dict:
    """
    Read positions.json.

    Returns dict with keys:
      capital, positions (dict), closed (list), daily (dict)

    Returns safe defaults when file is missing or corrupt.
    """
    data = _load_json(POSITIONS_FILE)
    return {
        "capital":   data.get("capital", 0.0),
        "positions": data.get("positions", {}),
        "closed":    data.get("closed", []),
        "daily":     data.get("daily", {"date": "", "pnl": 0.0}),
    }


# ── Status snapshot ───────────────────────────────────────────────────────────

def read_status_snapshot() -> dict:
    """
    Read status.json.

    Returns the full dict from status.json, or {} if unavailable.
    Key fields used by aggregator:
      running, capital, initial_capital, pnl, pnl_pct, cycle,
      scanned, candidates, open_positions, updated,
      positions, closed, decisions, orders
    """
    return _load_json(STATUS_FILE)


# ── Control state ─────────────────────────────────────────────────────────────

def read_control_state() -> dict:
    """
    Read control.json.

    Returns:
      live_trading (bool), simulation_running (bool), min_bet (float)
    """
    data = _load_json(CONTROL_FILE)
    return {
        "live_trading":        data.get("live_trading", False),
        "simulation_running":  data.get("simulation_running", False),
        "min_bet":             data.get("min_bet", 1.0),
    }


# ── Readiness verdict ─────────────────────────────────────────────────────────

def read_readiness_verdict() -> dict:
    """
    Read data/readiness_verdict.json written by monitoring/daily_review.py.

    Returns the full dict, or {"status": "NOT_REVIEWED"} if not present.

    Expected schema (from monitoring/daily_review.write_readiness_verdict):
      {
        "verdict": str,
        "evidence_sufficient": bool,
        "verdict_reason": str,
        "blockers": [...],
        "fails": [...],
        "warns": [...],
        "checks": [...],
        "pilot_constraints": {...},
        "generated_utc": str,
        "evidence_result": {...}
      }
    """
    data = _load_json(READINESS_FILE)
    if not data:
        return {"verdict": "NOT_REVIEWED", "generated_utc": None}
    return data


# ── Shadow journal files ───────────────────────────────────────────────────────

def find_journal_files() -> list[str]:
    """
    Return list of shadow_journal_*.jsonl file paths, most recent first.
    """
    files = glob.glob(JOURNAL_GLOB_PATTERN)
    return sorted(files, reverse=True)


def read_journal_records(
    max_records: int = 500,
    journal_files: Optional[list[str]] = None,
) -> list[dict]:
    """
    Read shadow journal records from JSONL files.

    Parameters
    ----------
    max_records : int
        Stop after reading this many records. Applied after sorting
        most-recent-first by ts_recorded_utc.
    journal_files : list[str] | None
        Specific files to read. If None, uses find_journal_files().

    Returns
    -------
    list[dict]
        Raw dicts from JSONL. Most recent first.
        Each dict maps directly to a ShadowDecisionRecord JSON structure.
    """
    if journal_files is None:
        journal_files = find_journal_files()

    records: list[dict] = []
    for path in journal_files:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass

    # Sort most-recent-first by ts_recorded_utc
    def _ts_key(r: dict) -> str:
        return r.get("ts_recorded_utc", "")

    records.sort(key=_ts_key, reverse=True)
    return records[:max_records]


def read_journal_integrity_stats(journal_files: Optional[list[str]] = None) -> dict:
    """
    Compute basic integrity statistics for journal files.

    Returns:
      total_lines, parsed_ok, bad_json_count, bad_line_fraction,
      files_checked, oldest_ts, newest_ts
    """
    if journal_files is None:
        journal_files = find_journal_files()

    total   = 0
    ok      = 0
    bad     = 0
    ts_list = []

    for path in journal_files:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    try:
                        rec = json.loads(line)
                        ok += 1
                        ts = rec.get("ts_recorded_utc", "")
                        if ts:
                            ts_list.append(ts)
                    except json.JSONDecodeError:
                        bad += 1
        except OSError:
            pass

    return {
        "total_lines":        total,
        "parsed_ok":          ok,
        "bad_json_count":     bad,
        "bad_line_fraction":  (bad / total) if total > 0 else 0.0,
        "files_checked":      len(journal_files),
        "oldest_ts":          min(ts_list) if ts_list else None,
        "newest_ts":          max(ts_list) if ts_list else None,
    }
