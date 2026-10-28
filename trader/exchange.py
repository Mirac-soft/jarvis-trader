"""BtcTurk bağlantısı — SALT-OKUNUR.

Bu sınıf kod düzeyinde sadece okuma işlemlerini dışarı açar: bakiye, açık emirler,
işlem geçmişi, komisyon. Emir verme / iptal / para çekme fonksiyonu YOKTUR.
Borsa nesnesi dışarıya verilmez; olmayan bir fonksiyon çağrılırsa hata fırlatır.

Anahtarlar sadece ~/.trader/secrets.env dosyasından okunur (OneDrive dışı),
asla loglanmaz, ekrana yazılmaz.
"""
from __future__ import annotations

import ccxt

from .safety import SafetyError, load_secrets

ALLOWED = ("balance", "open_orders", "my_trades", "trading_fees", "markets")


class ReadOnlyBtcTurk:
    __slots__ = ("_ex",)

    def __init__(self):
        sec = load_secrets()
        key, secret = sec.get("BTCTURK_API_KEY", ""), sec.get("BTCTURK_API_SECRET", "")
        if not key or not secret:
            raise SafetyError("API anahtarı yok: ~/.trader/secrets.env dosyasını doldur.")
        object.__setattr__(self, "_ex", ccxt.btcturk({"apiKey": key, "secret": secret, "enableRateLimit": True}))

    def __getattr__(self, name):
        raise SafetyError(f"'{name}' salt-okunur bağlantıda yok. Emir verme bu sınıfta bilerek bulunmaz.")

    def __setattr__(self, name, value):
        raise SafetyError("Salt-okunur bağlantı değiştirilemez.")

    def __repr__(self):
        return "<ReadOnlyBtcTurk>"            # anahtarlar asla repr'de görünmez

    def balance(self) -> dict:
        """Sıfır olmayan bakiyeler: {coin: {'toplam', 'kullanilabilir'}}"""
        b = self._ex.fetch_balance()
        return {c: {"toplam": b["total"][c], "kullanilabilir": b["free"].get(c, 0)}
                for c in b["total"] if b["total"][c]}

    def open_orders(self, symbol: str | None = None) -> list:
        return self._ex.fetch_open_orders(symbol)

    def my_trades(self, symbol: str, limit: int = 50) -> list:
        return self._ex.fetch_my_trades(symbol, limit=limit)

    def trading_fees(self) -> dict:
        try:
            return self._ex.fetch_trading_fees()
        except ccxt.NotSupported:
            return {}

    def markets(self) -> dict:
        return self._ex.load_markets()
