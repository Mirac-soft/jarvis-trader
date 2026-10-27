"""Ayar dosyasını okur ve güvenlik duvarından geçirir."""
from __future__ import annotations

import tomllib
from pathlib import Path

from .safety import validate_config

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "settings.toml"


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    validate_config(cfg)   # Sınır dışı ayarda burada durur
    return cfg
