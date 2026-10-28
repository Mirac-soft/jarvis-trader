"""Güvenlik duvarı: botun hiçbir ayarla aşamayacağı sabit sınırlar.

Ayar dosyası bu sınırların üstüne çıkarsa program hiç başlamaz.
Gizli anahtarlar sadece OneDrive dışındaki ~/.trader klasöründen okunur.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

HOME = Path(os.environ.get("TRADER_HOME", Path.home() / ".trader"))
KILL_SWITCH = HOME / "DUR"            # Bu dosya varsa bot hiçbir işlem açmaz.
SECRETS_FILE = HOME / "secrets.env"
CONSOLE_LOG = sys.stderr is not None   # penceresiz çalışmada kapalı

# Hiçbir ayarın aşamayacağı tavanlar. Değiştirmek kodu değiştirmeyi gerektirir.
HARD_LIMITS = {
    "risk_per_trade": 0.02,      # Tek işlemde sermayenin en fazla %2'si riske girer
    "max_position_fraction": 1.0,  # Sermayeden büyük pozisyon yok = kaldıraç yok
    "max_daily_loss": 0.05,      # Günlük zarar %5'i geçemez
    "max_drawdown": 0.30,        # Zirveden %30 düşüşte bot kendini kapatır
}
ALLOWED_MODES = {"backtest", "paper"}   # "live" henüz bilerek kapalı
ALLOWED_QUOTES = {"USDT", "TRY"}
ALLOWED_BASES = {"BTC", "ETH"}


MAX_LIVE_CAPITAL_USDT = 300.0          # Gerçek modda botlara verilebilecek en fazla sermaye (~15.000 TL)
LIVE_APPROVAL_FILE = HOME / "CANLI_ONAY"   # Kullanıcının bilinçli açtığı ikinci kilit


class SafetyError(RuntimeError):
    """Güvenlik kuralı ihlali: program durur, işlem yapılmaz."""


def live_requested(cfg: dict) -> bool:
    return bool(cfg.get("live", {}).get("enabled"))


def assert_live_allowed(cfg: dict) -> None:
    """Gerçek mod için iki kilit: ayarda enabled=true VE ~/.trader/CANLI_ONAY dosyası."""
    if not live_requested(cfg):
        raise SafetyError("Gerçek mod ayarda kapalı (live.enabled = false).")
    if not LIVE_APPROVAL_FILE.exists():
        raise SafetyError(f"Gerçek mod onay dosyası yok: {LIVE_APPROVAL_FILE}")


def validate_config(cfg: dict) -> None:
    mode = cfg["general"]["mode"]
    if mode not in ALLOWED_MODES:
        raise SafetyError(f"Mod '{mode}' izinli değil. İzinli: {sorted(ALLOWED_MODES)}")

    for symbol in [cfg["general"]["symbol"], *cfg["general"].get("symbols", [])]:
        base, _, quote = symbol.partition("/")
        if base not in ALLOWED_BASES or quote not in ALLOWED_QUOTES:
            raise SafetyError(f"'{symbol}' izinli semboller listesinde değil.")

    if not 1 <= cfg.get("turtle", {}).get("max_units", 1) <= 4:
        raise SafetyError("Kademeli alım en fazla 4 birim olabilir.")

    risk = cfg["risk"]
    for key, cap in HARD_LIMITS.items():
        value = risk[key]
        if not (0 < value <= cap):
            raise SafetyError(f"risk.{key}={value} izinli aralık dışında (0, {cap}].")

    rule = cfg.get("team", {}).get("rule", "bagimsiz")
    if rule not in {"bagimsiz", "buyuk_resim", "risk_paylasimi", "fren_dayanismasi", "hepsi"}:
        raise SafetyError(f"Bilinmeyen yardımlaşma kuralı: {rule}")

    if cfg.get("team", {}).get("alarms", "yok") not in {"yok", "trend", "trend_dusus", "tam"}:
        raise SafetyError("Bilinmeyen haberleşme ayarı (team.alarms).")

    bots = cfg.get("bots", {})
    if bots and abs(sum(b["capital_share"] for b in bots.values()) - 1.0) > 1e-9:
        raise SafetyError("Botların sermaye payları toplamı %100 olmalı.")
    for name, b in bots.items():
        for key in ("risk_per_trade", "max_drawdown"):
            if not (0 < b[key] <= HARD_LIMITS[key]):
                raise SafetyError(f"bots.{name}.{key}={b[key]} izinli aralık dışında (0, {HARD_LIMITS[key]}].")

    live = cfg.get("live")
    if live:
        if not 0 < live["capital_usdt"] <= MAX_LIVE_CAPITAL_USDT:
            raise SafetyError(f"live.capital_usdt (0, {MAX_LIVE_CAPITAL_USDT:.0f}] aralığında olmalı.")
        if not 0 < live["max_order_usdt"] <= live["capital_usdt"]:
            raise SafetyError("live.max_order_usdt sermayeden büyük olamaz.")
        if not 0 < live["price_band"] <= 0.02 or not 0 < live["limit_slip"] <= 0.01:
            raise SafetyError("live.price_band <= %2 ve live.limit_slip <= %1 olmalı.")
        if not 5 <= live["approval_ttl_min"] <= 240:
            raise SafetyError("live.approval_ttl_min 5-240 dakika olmalı.")

    costs = cfg["costs"]
    if costs["fee"] < 0.001 or costs["slippage"] < 0.0002:
        # Maliyeti olduğundan düşük göstermek testleri yalancı iyi yapar.
        raise SafetyError("Komisyon/kayma gerçekçi olmayan derecede düşük.")


def kill_switch_active() -> bool:
    return KILL_SWITCH.exists()


def assert_outside_onedrive(path: Path) -> None:
    if "onedrive" in str(path.resolve()).lower():
        raise SafetyError(f"Gizli dosya OneDrive içinde olamaz: {path}")


def load_secrets() -> dict[str, str]:
    """~/.trader/secrets.env dosyasını okur. Değerler asla loglanmaz."""
    assert_outside_onedrive(SECRETS_FILE)
    if not SECRETS_FILE.exists():
        return {}
    secrets = {}
    for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", line)
        if m:
            secrets[m.group(1)] = m.group(2).strip()
    return secrets


class RedactSecrets(logging.Filter):
    """Loglara yanlışlıkla düşen anahtar benzeri uzun dizileri maskeler."""

    pattern = re.compile(r"[A-Za-z0-9+/=_-]{32,}")

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.pattern.sub("***GIZLI***", str(record.msg))
        return True


def setup_logging(name: str = "trader") -> logging.Logger:
    HOME.joinpath("logs").mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(name)
    if not log.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        handlers = [logging.FileHandler(HOME / "logs" / f"{name}.log", encoding="utf-8")]
        if CONSOLE_LOG and sys.stderr is not None:   # penceresiz (pythonw) çalışmada konsol yok
            handlers.append(logging.StreamHandler())
        for h in handlers:
            h.setFormatter(fmt)
            h.addFilter(RedactSecrets())
            log.addHandler(h)
        log.setLevel(logging.INFO)
    return log
