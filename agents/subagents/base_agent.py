"""
BaseAgent — Tum subagent'larin abstract base class'i.

Her agent:
- Kendi ismine ve durumuna sahip
- async run() ile calisir
- AgentResult doner
- Hata durumunda graceful degrade eder
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger


class AgentStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


@dataclass
class AgentResult:
    """Her agent'in dondugu standart sonuc."""
    agent_name: str
    status: AgentStatus
    data: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return self.status == AgentStatus.COMPLETED

    def __repr__(self) -> str:
        s = f"AgentResult({self.agent_name}, {self.status.value}"
        if self.duration_ms:
            s += f", {self.duration_ms:.0f}ms"
        if self.error:
            s += f", err={self.error[:60]}"
        return s + ")"


class BaseAgent(ABC):
    """Abstract base for all subagents."""

    def __init__(self, name: str, timeout_seconds: float = 30.0):
        self.name = name
        self.timeout_seconds = timeout_seconds
        self._status = AgentStatus.IDLE
        self._last_run: float = 0.0
        self._run_count: int = 0
        self._total_duration_ms: float = 0.0

    @property
    def status(self) -> AgentStatus:
        return self._status

    @property
    def avg_duration_ms(self) -> float:
        if self._run_count == 0:
            return 0.0
        return self._total_duration_ms / self._run_count

    async def execute(self, **kwargs) -> AgentResult:
        """Run the agent with timeout and error handling."""
        self._status = AgentStatus.RUNNING
        start = time.monotonic()

        try:
            result_data = await asyncio.wait_for(
                self.run(**kwargs),
                timeout=self.timeout_seconds,
            )
            duration_ms = (time.monotonic() - start) * 1000
            self._status = AgentStatus.COMPLETED
            self._run_count += 1
            self._total_duration_ms += duration_ms
            self._last_run = time.time()

            logger.info(
                f"[{self.name}] completed in {duration_ms:.0f}ms "
                f"(avg={self.avg_duration_ms:.0f}ms, runs={self._run_count})"
            )

            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.COMPLETED,
                data=result_data,
                duration_ms=duration_ms,
            )

        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            self._status = AgentStatus.TIMEOUT
            logger.warning(f"[{self.name}] TIMEOUT after {self.timeout_seconds}s")
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.TIMEOUT,
                error=f"Timeout after {self.timeout_seconds}s",
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            self._status = AgentStatus.FAILED
            logger.error(f"[{self.name}] FAILED: {e}")
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=str(e),
                duration_ms=duration_ms,
            )

    @abstractmethod
    async def run(self, **kwargs) -> Any:
        """Implement in subclass. Return the agent's result data."""
        ...
