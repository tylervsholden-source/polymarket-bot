"""
Approval Queue — Emir onay state machine.

INC-2026-03-15-001 dersi: Sinyal → emir arasında insan onayı ZORUNLU.

State machine:
  PENDING → APPROVED | REJECTED | EXPIRED
  APPROVED → EXECUTED | EXECUTION_BLOCKED | EXPIRED
  Terminal: REJECTED, EXPIRED, EXECUTED, EXECUTION_BLOCKED
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from loguru import logger

from control_plane.types import ApprovalState

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PENDING_FILE = os.path.join(_BASE_DIR, "data", "pending_orders.json")
DEFAULT_TIMEOUT_SEC = 300  # 5 dakika


class ApprovalQueue:
    """File-based approval queue with state machine and file locking."""

    def __init__(self, pending_file: str = DEFAULT_PENDING_FILE, timeout_sec: float = DEFAULT_TIMEOUT_SEC):
        self._file = pending_file
        self._lock_file = pending_file + ".lock"
        self._timeout = timeout_sec

    @contextmanager
    def _file_lock(self):
        """Cross-platform file lock to prevent race conditions between web server thread and asyncio thread."""
        os.makedirs(os.path.dirname(self._lock_file), exist_ok=True)
        lock_fd = None
        try:
            lock_fd = open(self._lock_file, "w")
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            if lock_fd:
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except Exception:
                    pass
                lock_fd.close()

    def _load(self) -> list[dict]:
        try:
            with open(self._file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, orders: list[dict]) -> None:
        os.makedirs(os.path.dirname(self._file), exist_ok=True)
        with open(self._file, "w") as f:
            json.dump(orders, f, indent=2, ensure_ascii=False)

    def enqueue(self, order_request: dict) -> str:
        """Yeni emir isteğini PENDING olarak kuyruğa ekle. order_id döner."""
        with self._file_lock():
            orders = self._load()

            # Dedup: aynı market_id zaten PENDING mi?
            market_id = order_request.get("market_id", "")
            for o in orders:
                if o.get("market_id") == market_id and o.get("status") == ApprovalState.PENDING:
                    logger.debug(f"Market {market_id} zaten kuyrukta, atlanıyor.")
                    return o["id"]

            order_id = str(uuid.uuid4())[:8]
            entry = {
                "id": order_id,
                "status": ApprovalState.PENDING,
                "enqueued_at": time.time(),
                "market_id": market_id,
                "question": order_request.get("question", ""),
                "direction": order_request.get("direction", ""),
                "amount": order_request.get("amount", 0),
                "entry_price": order_request.get("entry_price", 0),
                "edge": order_request.get("edge", 0),
                "bayesian_prob": order_request.get("bayesian_prob", 0),
                "token_id": order_request.get("token_id", ""),
                "end_date_iso": order_request.get("end_date_iso", ""),
                "state_history": [
                    {"state": ApprovalState.PENDING, "at": time.time(), "reason": "enqueued"},
                ],
            }
            orders.append(entry)
            self._save(orders)
            logger.info(f"Emir kuyruğa eklendi [{order_id}]: {entry['question'][:50]} ${entry['amount']:.2f}")
            return order_id

    def transition(self, order_id: str, to_state: ApprovalState, reason: str = "") -> bool:
        """State geçişi yap. Geçersiz geçişlerde False döner."""
        with self._file_lock():
            orders = self._load()
            for o in orders:
                if o["id"] != order_id:
                    continue
                current = ApprovalState(o["status"])
                if not current.can_transition_to(to_state):
                    logger.warning(
                        f"Geçersiz geçiş [{order_id}]: {current.value} → {to_state.value}"
                    )
                    return False
                o["status"] = to_state.value
                o.setdefault("state_history", []).append({
                    "state": to_state.value, "at": time.time(), "reason": reason,
                })
                if to_state == ApprovalState.APPROVED:
                    o["approved_at"] = time.time()
                elif to_state == ApprovalState.EXECUTED:
                    o["executed_at"] = time.time()
                self._save(orders)
                logger.info(f"Emir [{order_id}] {current.value} → {to_state.value}: {reason}")
                return True
            return False

    # ── Convenience methods (delegate to transition) ──

    def approve(self, order_id: str) -> bool:
        return self.transition(order_id, ApprovalState.APPROVED, "operator_approved")

    def reject(self, order_id: str) -> bool:
        return self.transition(order_id, ApprovalState.REJECTED, "operator_rejected")

    def mark_executed(self, order_id: str) -> None:
        self.transition(order_id, ApprovalState.EXECUTED, "order_executed")

    def block_execution(self, order_id: str, reason: str) -> bool:
        return self.transition(order_id, ApprovalState.EXECUTION_BLOCKED, reason)

    # ── Query methods ──

    def get_by_state(self, state: ApprovalState) -> list[dict]:
        return [o for o in self._load() if o.get("status") == state.value]

    def get_pending(self) -> list[dict]:
        return self.get_by_state(ApprovalState.PENDING)

    def get_approved(self) -> list[dict]:
        return self.get_by_state(ApprovalState.APPROVED)

    def get_all(self) -> list[dict]:
        return self._load()

    # ── Maintenance ──

    def cleanup_expired(self, timeout_sec: float | None = None) -> int:
        """Süresi dolmuş PENDING ve APPROVED emirleri EXPIRED yap. Kaç tane expire ettiyse döner."""
        with self._file_lock():
            timeout = timeout_sec or self._timeout
            orders = self._load()
            now = time.time()
            count = 0
            for o in orders:
                state = o.get("status", "")
                if state == ApprovalState.PENDING:
                    if (now - o.get("enqueued_at", 0)) > timeout:
                        o["status"] = ApprovalState.EXPIRED
                        o.setdefault("state_history", []).append({
                            "state": ApprovalState.EXPIRED, "at": now, "reason": "timeout",
                        })
                        count += 1
                        logger.info(f"Emir süresi doldu [{o['id']}]: {o.get('question', '')[:50]}")
                elif state == ApprovalState.APPROVED:
                    if (now - o.get("approved_at", 0)) > timeout:
                        o["status"] = ApprovalState.EXPIRED
                        o.setdefault("state_history", []).append({
                            "state": ApprovalState.EXPIRED, "at": now, "reason": "approval_timeout",
                        })
                        count += 1
                        logger.info(f"Onay süresi doldu [{o['id']}]: {o.get('question', '')[:50]}")
            if count:
                self._save(orders)
            return count


# ── Module-level singleton (backward compat with core/approval_queue.py) ──
_default_queue = ApprovalQueue()

def enqueue(order_request: dict) -> str:
    return _default_queue.enqueue(order_request)

def approve(order_id: str) -> bool:
    return _default_queue.approve(order_id)

def reject(order_id: str) -> bool:
    return _default_queue.reject(order_id)

def get_approved() -> list[dict]:
    return _default_queue.get_approved()

def get_pending() -> list[dict]:
    return _default_queue.get_pending()

def get_all() -> list[dict]:
    return _default_queue.get_all()

def mark_executed(order_id: str) -> None:
    _default_queue.mark_executed(order_id)

def cleanup_expired(timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> None:
    _default_queue.cleanup_expired(timeout_sec)

def block_execution(order_id: str, reason: str) -> bool:
    return _default_queue.block_execution(order_id, reason)
