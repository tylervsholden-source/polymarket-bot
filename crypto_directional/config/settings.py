"""
crypto_directional/config/settings.py

Config loader — defaults.yaml'ı okur, ortam değişkeni ve runtime override destekler.

Polymarket botunun config yapısından bağımsızdır.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_DEFAULT_YAML = Path(__file__).parent / "defaults.yaml"


class Settings:
    """
    Hiyerarşik config yükleyici:
    1. defaults.yaml (temel)
    2. Ortam değişkenleri (CD_ prefix, örn: CD_RISK__DAILY_LOSS_LIMIT_PCT)
    3. Runtime override (Settings.override() ile)

    Kullanım:
        from crypto_directional.config.settings import settings
        threshold = settings.get("thresholds.taker_fee_pct")
        capital   = settings.get("capital.initial")
    """

    def __init__(self, yaml_path: Path = _DEFAULT_YAML) -> None:
        self._data: dict = {}
        self._load_yaml(yaml_path)
        self._apply_env_overrides()

    def _load_yaml(self, path: Path) -> None:
        try:
            import yaml
            with open(path, encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}
        except ImportError:
            raise RuntimeError(
                "PyYAML kurulu değil: pip install pyyaml"
            )
        except FileNotFoundError:
            raise RuntimeError(f"Config dosyası bulunamadı: {path}")

    def _apply_env_overrides(self) -> None:
        """
        CD_ prefix'li ortam değişkenlerini config'e yazar.
        Çift alt çizgi (__) iç içe key ayraç olarak kullanılır.

        Örnek:
            CD_RISK__DAILY_LOSS_LIMIT_PCT=0.03
            → config["risk"]["daily_loss_limit_pct"] = 0.03
        """
        prefix = "CD_"
        for key, val in os.environ.items():
            if not key.startswith(prefix):
                continue
            parts = key[len(prefix):].lower().split("__")
            self._set_nested(self._data, parts, self._cast(val))

    @staticmethod
    def _cast(val: str) -> Any:
        """String → Python tipine dönüştür."""
        if val.lower() in ("true", "yes"):
            return True
        if val.lower() in ("false", "no"):
            return False
        try:
            return int(val)
        except ValueError:
            pass
        try:
            return float(val)
        except ValueError:
            pass
        return val

    @staticmethod
    def _set_nested(d: dict, keys: list[str], value: Any) -> None:
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value

    def get(self, key: str, default: Any = None) -> Any:
        """
        Nokta-ayrımlı key ile değer getir.
        Örnek: settings.get("risk.daily_loss_limit_pct")
        """
        parts = key.split(".")
        node = self._data
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, key: str) -> Any:
        """
        Değer yoksa RuntimeError fırlatır.
        Zorunlu parametreler için kullan.
        """
        val = self.get(key)
        if val is None:
            raise RuntimeError(f"Zorunlu config değeri eksik: '{key}'")
        return val

    def override(self, key: str, value: Any) -> None:
        """
        Runtime override (test ve paper trading için).
        Production'da kullanılmamalı.
        """
        parts = key.split(".")
        self._set_nested(self._data, parts, value)

    def label_threshold(self, horizon: str) -> float:
        """
        Verilen horizon için label eşiği döner.

        Her horizon için ayrı config anahtarı kullanılır:
          thresholds.label_threshold_5m
          thresholds.label_threshold_15m

        Bu değerler başlangıçta eşittir (taker+slip+edge ≈ 0.00075).
        Faz 3 walk-forward backtest sonrası veriye dayalı kalibre edilir.
        Keyfi çarpan (ör: 1.5×) kullanılmaz.
        """
        key = f"thresholds.label_threshold_{horizon}"
        val = self.get(key)
        if val is not None:
            return float(val)
        # Fallback: config anahtarı yoksa bileşenlerden hesapla
        taker    = self.require("thresholds.taker_fee_pct")
        slip     = self.require("thresholds.slippage_pct")
        min_edge = self.require("thresholds.min_edge_pct")
        return round(taker + slip + min_edge, 6)

    def as_dict(self) -> dict:
        """Tüm config'i dict olarak döner (sadece okuma amaçlı)."""
        import copy
        return copy.deepcopy(self._data)

    def __repr__(self) -> str:
        return f"Settings(keys={list(self._data.keys())})"


# Singleton — modül düzeyinde tek instance
settings = Settings()
