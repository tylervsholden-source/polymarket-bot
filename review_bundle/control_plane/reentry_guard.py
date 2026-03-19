"""
Reentry Guard — Aynı market'e tekrar giriş engeli.

INC-2026-03-15-001 dersi: Bot aynı market'e 4 kez emir verdi (DOGE).
Pozisyon açılıp hemen kapanınca cooldown yoktu, döngü tekrarlandı.
"""
from __future__ import annotations

import json
import os
import time

from loguru import logger


class ReentryGuard:
    """Per-market cooldown + session lockout.

    İki katman:
    1. Session set: Bu çalışmada kapanan market_id'ler (bellek)
    2. Persistent file: 24 saatlik cooldown (dosya)
    """

    def __init__(
        self,
        cooldown_file: str = "data/market_cooldowns.json",
        cooldown_hours: float = 24.0,
    ):
        self._file = cooldown_file
        self._cooldown_sec = cooldown_hours * 3600
        self._session_blocked: set[str] = set()
        self._load()

    def _load(self) -> None:
        """Başlangıçta kalıcı cooldown'ları yükle."""
        try:
            with open(self._file) as f:
                data = json.load(f)
            cutoff = time.time() - self._cooldown_sec
            for mid, ts in data.items():
                if ts > cutoff:
                    self._session_blocked.add(mid)
            if self._session_blocked:
                logger.info(f"Kalıcı cooldown yüklendi: {len(self._session_blocked)} market.")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def is_blocked(self, market_id: str) -> bool:
        """Market cooldown'da mı?"""
        return market_id in self._session_blocked

    def mark_closed(self, market_id: str) -> None:
        """Market kapandı — cooldown'a ekle (session + persistent)."""
        self._session_blocked.add(market_id)
        self._persist(market_id)

    def mark_traded(self, market_id: str) -> None:
        """Market'e emir verildi — cooldown'a ekle."""
        self._session_blocked.add(market_id)
        self._persist(market_id)

    def blocked_markets(self) -> set[str]:
        """Dashboard için bloke market listesi."""
        return set(self._session_blocked)

    def blocked_count(self) -> int:
        return len(self._session_blocked)

    def clear(self, market_id: str) -> None:
        """Manuel override: cooldown'u kaldır."""
        self._session_blocked.discard(market_id)
        self._remove_persistent(market_id)
        logger.info(f"Cooldown temizlendi: {market_id}")

    def cleanup_stale(self) -> int:
        """Süresi dolmuş cooldown'ları temizle."""
        try:
            with open(self._file) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return 0

        cutoff = time.time() - self._cooldown_sec
        before = len(data)
        data = {k: v for k, v in data.items() if v > cutoff}
        after = len(data)

        if before != after:
            os.makedirs(os.path.dirname(self._file), exist_ok=True)
            with open(self._file, "w") as f:
                json.dump(data, f)

        # Session set'i de güncelle
        self._session_blocked = {mid for mid in self._session_blocked if mid in data}
        return before - after

    def _persist(self, market_id: str) -> None:
        """Cooldown'u dosyaya yaz."""
        try:
            with open(self._file) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        data[market_id] = time.time()
        # Eski olanları temizle
        cutoff = time.time() - self._cooldown_sec
        data = {k: v for k, v in data.items() if v > cutoff}
        os.makedirs(os.path.dirname(self._file), exist_ok=True)
        with open(self._file, "w") as f:
            json.dump(data, f)

    def _remove_persistent(self, market_id: str) -> None:
        try:
            with open(self._file) as f:
                data = json.load(f)
            data.pop(market_id, None)
            with open(self._file, "w") as f:
                json.dump(data, f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
