"""
Control Plane type definitions.

Tüm güvenlik modüllerinin ortak tipleri.
INC-2026-03-15-001 sonrası: string-based state → enum-based state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ApprovalState(str, Enum):
    """Emir onay durumu state machine.

    Geçiş kuralları:
      PENDING → APPROVED | REJECTED | EXPIRED
      APPROVED → EXECUTED | EXECUTION_BLOCKED | EXPIRED
      Diğer geçişler yasak.
    """
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    EXECUTION_BLOCKED = "execution_blocked"

    # Geçerli geçişler: {kaynak: {hedef, ...}}
    @staticmethod
    def valid_transitions() -> dict[ApprovalState, set[ApprovalState]]:
        S = ApprovalState
        return {
            S.PENDING: {S.APPROVED, S.REJECTED, S.EXPIRED},
            S.APPROVED: {S.EXECUTED, S.EXECUTION_BLOCKED, S.EXPIRED},
            # Terminal durumlar — geçiş yok
            S.REJECTED: set(),
            S.EXPIRED: set(),
            S.EXECUTED: set(),
            S.EXECUTION_BLOCKED: set(),
        }

    def can_transition_to(self, target: ApprovalState) -> bool:
        return target in self.valid_transitions().get(self, set())


@dataclass
class LiveGateCheck:
    """Tek bir güvenlik kontrol sonucu."""
    name: str
    passed: bool
    reason: str = ""


@dataclass
class LiveGateResult:
    """10-nokta güvenlik kontrolünün toplu sonucu."""
    passed: bool
    checks: list[LiveGateCheck] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def blockers(self) -> list[str]:
        return [c.reason for c in self.checks if not c.passed]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checked_at": self.checked_at.isoformat(),
            "blockers": self.blockers,
            "checks": {c.name: {"passed": c.passed, "reason": c.reason} for c in self.checks},
        }


@dataclass
class ExpiryRejection:
    """Market expiry red sebebi."""
    market_id: str
    reason: str  # EXPIRED, TOO_NEAR, TOO_FAR, NO_END_DATE
    hours_to_close: float | None = None
    question: str = ""


@dataclass
class ProcessLockInfo:
    """Process lock durumu."""
    pid: int
    lock_file: str
    acquired_at: float = 0.0
    is_current: bool = False
