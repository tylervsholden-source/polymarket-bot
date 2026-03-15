"""
shared/config/yaml_loader.py

Genel amaçlı YAML config yükleyici.
crypto_directional ve ileride eklenecek modüller kullanabilir.
Polymarket botuna özgü bir şey içermez.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def load_yaml(path: str | Path) -> dict:
    """YAML dosyasını dict olarak yükler."""
    try:
        import yaml
    except ImportError:
        raise RuntimeError("PyYAML kurulu değil: pip install pyyaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_nested(data: dict, key: str, default: Any = None) -> Any:
    """
    Nokta-ayrımlı key ile iç içe dict'ten değer getir.
    Örnek: get_nested(cfg, "risk.daily_loss_limit_pct")
    """
    parts = key.split(".")
    node = data
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
