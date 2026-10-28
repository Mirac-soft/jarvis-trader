"""Gerçek emir aracısı (canlı mod). Botlar borsaya SADECE bu katmanla ulaşır.

Güvenlik duvarları:
- Sadece ayarda izinli coinler; tek emirde `live.max_order_usdt` tavanı.
- Alım: fiyatı sınırlı emir (en fazla `limit_slip` kötü fiyat), fiyat bandı kontrolü,
  20 sn'de dolmayan kısım iptal. Onaysız alım bu sınıfa ULAŞAMAZ (onay, live_portfolio'da).
- Satış (çıkış garantili): önce borsadaki stop iptal edilir, fiyatı sınırlı satış;
  dolmayan kısım piyasa fiyatından satılır.
- Borsada bekleyen stop emri (stopMarket): bilgisayar kapalıyken de zarar-kes çalışır.
- Gerçek maliyet bakiye FARKINDAN hesaplanır (komisyon + KDV dahil, tahmin yok).
- İki bot aynı hesabı kullandığı için tüm emir işlemleri hesap kilidiyle SIRAYA girer.
"""
from __future__ import annotations

import msvcrt
import time
from contextlib import contextmanager
from dataclasses import dataclass

import ccxt

from .net import force_ipv4
from .safety import HOME, SafetyError, load_secrets, setup_logging

force_ipv4()
log = setup_logging("broker")
LOCK_FILE = HOME / "run" / "hesap.lock"


@dataclass
class Fill:
    qty: float          # hesaba giren/çıkan coin (net)
    avg: float          # ortalama fiyat
    quote: float        # alımda ödenen, satımda alınan USDT (komisyon/KDV dahil, bakiye farkından)
    by_stop: bool = False


@contextmanager
def account_lock(timeout=120):
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_FILE, "a+")
    f.seek(0)
    deadline = time.time() + timeout
    while True:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            if time.time() > deadline:
                f.close()
                raise TimeoutError("hesap kilidi alınamadı")
            time.sleep(0.3)
    try:
        yield
    finally:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()


class LiveBroker:
    def __init__(self, cfg: dict, ex=None, sleep=time.sleep):
        live = cfg["live"]
        self.allowed = set(cfg["general"]["symbols"])
        self.max_order = float(live["max_order_usdt"])
        self.band = float(live["price_band"])
        self.slip = float(live["limit_slip"])
        self.wait = int(live.get("fill_wait_sec", 20))
        self.sleep = sleep
        if ex is None:
            sec = load_secrets()
            if not sec.get("BTCTURK_API_KEY") or not sec.get("BTCTURK_API_SECRET"):
                raise SafetyError("API anahtarı yok.")
            ex = ccxt.btcturk({"apiKey": sec["BTCTURK_API_KEY"], "secret": sec["BTCTURK_API_SECRET"],
                               "enableRateLimit": True})
        self.ex = ex
        self.markets = self.ex.load_markets()

    def __repr__(self):
        return "<LiveBroker>"

    # ---- yardımcılar ---------------------------------------------------------
    def _check_symbol(self, s):
        if s not in self.allowed:
            raise SafetyError(f"{s} canlı işlemde izinli değil.")

    def _min_cost(self, s):
        return float(self.markets[s]["limits"]["cost"]["min"] or 10.0)

    def quote(self, s):
        ob = self.ex.fetch_order_book(s, 5)
        return float(ob["bids"][0][0]), float(ob["asks"][0][0])

    def balances(self, s):
        base, quote = s.split("/")
        b = self.ex.fetch_balance()
        return float(b["total"].get(base) or 0), float(b["total"].get(quote) or 0), float(b["free"].get(quote) or 0)

    def check_trade_permission(self):
        """Var olmayan emri iptal etmeyi dener: izin yoksa borsa 'yetkisiz' der. Hiçbir şeyi etkilemez."""
        try:
            self.ex.cancel_order("1", next(iter(self.allowed)))
        except ccxt.AuthenticationError as e:
            raise SafetyError("API anahtarında Al-Sat izni yok: canlı mod başlatılamaz.") from e
        except ccxt.BaseError:
            pass                                    # 'emir bulunamadı' = izin var

    def _wait_fill(self, s, oid):
        deadline = time.time() + self.wait
        while time.time() < deadline:
            self.sleep(2)
            if not any(str(o["id"]) == oid for o in self.ex.fetch_open_orders(s)):
                return True
        try:
            self.ex.cancel_order(oid, s)
        except ccxt.OrderNotFound:
            return True                             # iptal anında dolmuş
        self.sleep(1)
        return False

    # ---- alım (sadece onaylı akıştan çağrılır) -----------------------------------
    def buy(self, s, usdt, ref_price) -> Fill | None:
        self._check_symbol(s)
        if usdt > self.max_order:
            raise SafetyError(f"Emir {usdt:.2f} USDT: tavan {self.max_order:.0f} USDT.")
        if usdt < self._min_cost(s):
            raise SafetyError(f"Emir {usdt:.2f} USDT: borsa minimumu {self._min_cost(s):.2f}.")
        with account_lock():
            bid, ask = self.quote(s)
            if abs(ask / ref_price - 1) > self.band:
                raise SafetyError(f"Fiyat referanstan %{abs(ask / ref_price - 1) * 100:.2f} uzak: alım yok.")
            base0, quote0, free_q = self.balances(s)
            if free_q < usdt * 1.01:
                raise SafetyError(f"Yetersiz bakiye: {free_q:.2f} var, {usdt * 1.01:.2f} gerekli.")
            limit = float(self.ex.price_to_precision(s, ask * (1 + self.slip)))
            qty = float(self.ex.amount_to_precision(s, usdt / limit))
            oid = str(self.ex.create_order(s, "limit", "buy", qty, limit)["id"])
            log.info("ALIM EMRI %s %s adet %.8f limit %.2f", oid, s, qty, limit)
            self._wait_fill(s, oid)
            base1, quote1, _ = self.balances(s)
        got, paid = base1 - base0, quote0 - quote1
        if got <= 0:
            log.info("ALIM DOLMADI %s", oid)
            return None
        return Fill(got, paid / got, paid)

    # ---- satış (çıkış garantili) ------------------------------------------------
    def sell(self, s, qty, ex_stop_id=None) -> Fill | None:
        self._check_symbol(s)
        with account_lock():
            if ex_stop_id:
                filled = self._cancel_or_detect_stop(s, ex_stop_id)
                if filled:
                    return filled
            base0, quote0, _ = self.balances(s)
            qty = float(self.ex.amount_to_precision(s, min(qty, base0)))
            bid, _ = self.quote(s)
            if qty <= 0 or qty * bid < self._min_cost(s):
                log.info("SATIS ATLANDI %s: miktar borsa minimumunun altında (%.8f)", s, qty)
                return None
            limit = float(self.ex.price_to_precision(s, bid * (1 - self.slip)))
            oid = str(self.ex.create_order(s, "limit", "sell", qty, limit)["id"])
            log.info("SATIS EMRI %s %s adet %.8f limit %.2f", oid, s, qty, limit)
            if not self._wait_fill(s, oid):
                base_mid, _, _ = self.balances(s)
                left = float(self.ex.amount_to_precision(s, base_mid - (base0 - qty)))
                if left > 0 and left * bid >= self._min_cost(s):
                    self.ex.create_order(s, "market", "sell", left)
                    log.info("SATIS KALANI PIYASADAN %s adet %.8f", s, left)
                    self.sleep(2)
            base1, quote1, _ = self.balances(s)
        sold, got = base0 - base1, quote1 - quote0
        return Fill(sold, got / sold, got) if sold > 0 else None

    # ---- borsada bekleyen stop ----------------------------------------------------
    def place_stop(self, s, qty, stop_price) -> str:
        self._check_symbol(s)
        with account_lock():
            stop_price = float(self.ex.price_to_precision(s, stop_price))
            qty = float(self.ex.amount_to_precision(s, qty))
            o = self.ex.create_order(s, "stopMarket", "sell", qty, stop_price, {"stopPrice": stop_price})
            log.info("BORSA STOP %s %s adet %.8f stop %.2f", o["id"], s, qty, stop_price)
            return str(o["id"])

    def replace_stop(self, s, old_id, qty, stop_price):
        """Stopu yukarı taşı. Eski stop bu arada dolmuşsa Fill döner (pozisyon kapanmış)."""
        if old_id:
            with account_lock():
                filled = self._cancel_or_detect_stop(s, old_id)
            if filled:
                return filled
        return self.place_stop(s, qty, stop_price)

    def stop_filled(self, s, stop_id):
        """None: stop bekliyor | Fill: stop doldu | 'kayip': stop yok ve dolmamış (yeniden konmalı)."""
        if any(str(o["id"]) == str(stop_id) for o in self.ex.fetch_open_orders(s)):
            return None
        return self._fill_from_trades(s, stop_id) or "kayip"

    def _cancel_or_detect_stop(self, s, stop_id) -> Fill | None:
        try:
            self.ex.cancel_order(str(stop_id), s)
            self.sleep(1)
            return None
        except ccxt.OrderNotFound:
            return self._fill_from_trades(s, stop_id)

    def _fill_from_trades(self, s, oid) -> Fill | None:
        fills = [t for t in self.ex.fetch_my_trades(s, limit=50) if str(t.get("order")) == str(oid)]
        if not fills:
            return None
        qty = sum(float(t["amount"]) for t in fills)
        got = sum(float(t["cost"]) - float((t.get("fee") or {}).get("cost") or 0) for t in fills)
        return Fill(qty, got / qty, got, by_stop=True)
