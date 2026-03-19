"""
Expiry Guard — Expired / near-expiry market reddi.

INC-2026-03-15-001 dersi: Expired market'lere emir verildi.
_hours_to_close() None dönüyordu, filtre geçiriyordu.

3 katmanlı savunma:
1. get_active_markets() → end_dt > now
2. _pre_filter() → h <= 0 kontrolü
3. ExpiryGuard.check() → açık red sebebi
"""
from __future__ import annotations

from datetime import datetime, timezone

from control_plane.types import ExpiryRejection


class ExpiryGuard:
    """Market expiry kontrol kapısı.

    Rejection reasons:
      EXPIRED — market süresi dolmuş (h <= 0)
      TOO_NEAR — kapanışa çok yakın (h < min_hours)
      TOO_FAR — kapanışa çok uzak (h > max_hours)
      NO_END_DATE — end_date bilgisi yok
    """

    def __init__(self, min_hours: float = 0.0, max_hours: float = 24.0):
        self.min_hours = min_hours
        self.max_hours = max_hours

    def hours_to_close(self, market: dict) -> float | None:
        """Market'in kapanışına kalan saat. Negatif = expired. None = bilinmiyor."""
        end_date = market.get("end_date_iso") or ""
        if not end_date:
            return None
        try:
            s = str(end_date).replace("Z", "+00:00")
            if len(s) == 10:
                s += "T23:59:00+00:00"
            end_dt = datetime.fromisoformat(s)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            delta = end_dt - datetime.now(timezone.utc)
            return delta.total_seconds() / 3600
        except Exception:
            return None

    def check(self, market: dict) -> ExpiryRejection | None:
        """Market expiry kontrolü. Geçerse None, red varsa ExpiryRejection döner."""
        market_id = market.get("condition_id", market.get("market_id", ""))
        question = market.get("question", "")

        h = self.hours_to_close(market)
        if h is None:
            return ExpiryRejection(
                market_id=market_id,
                reason="NO_END_DATE",
                hours_to_close=None,
                question=question,
            )
        if h <= 0:
            return ExpiryRejection(
                market_id=market_id,
                reason="EXPIRED",
                hours_to_close=h,
                question=question,
            )
        if h < self.min_hours:
            return ExpiryRejection(
                market_id=market_id,
                reason="TOO_NEAR",
                hours_to_close=h,
                question=question,
            )
        if h > self.max_hours:
            return ExpiryRejection(
                market_id=market_id,
                reason="TOO_FAR",
                hours_to_close=h,
                question=question,
            )
        return None  # Geçti

    def filter_markets(self, markets: list[dict]) -> tuple[list[dict], list[ExpiryRejection]]:
        """Toplu filtre: (geçenler, reddedilenler)."""
        passed = []
        rejected = []
        for m in markets:
            rej = self.check(m)
            if rej:
                rejected.append(rej)
            else:
                passed.append(m)
        return passed, rejected
