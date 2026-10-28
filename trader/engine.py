"""İşlem motoru: hem backtest hem canlı (paper) bot AYNI kodu kullanır.

Böylece testte görülen davranış ile canlıdaki davranış birebir aynıdır.

Bir bar iki aşamada işlenir:
  open_phase  -> önceki kapanışta verilen kararı uygula (alım/satış/ekleme)
  close_phase -> zarar-kes, iz süren stop, risk frenleri, yeni kararlar
Canlıda ayrıca check_stop() fiyatı saniyeler içinde izler.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .risk import RiskManager
from .safety import kill_switch_active


@dataclass
class Trade:
    symbol: str
    entry_time: object
    exit_time: object
    entry: float          # ortalama giriş fiyatı
    exit: float
    qty: float
    pnl: float
    reason: str
    units: int = 1


@dataclass
class Book:
    """Tek bir coin için açık pozisyon defteri."""
    qty: float = 0.0
    cost: float = 0.0
    units: int = 0
    stop: float = 0.0
    best: float = 0.0
    last_add: float = 0.0
    entry_t: object = None
    pending: str | None = None     # "in" | "add" | "out"
    pend_stop: float = 0.0
    cooldown: int = 0
    ex_stop_id: str | None = None  # canlı: borsada bekleyen stop emrinin numarası
    ex_stop_price: float = 0.0


@dataclass
class Portfolio:
    cfg: dict
    start_equity: float
    symbols: list
    max_units: int = 1
    live: bool = False                       # True: acil durdurma dosyasına uyar
    on_event: object = None                  # canlıda olayları kaydetmek için
    gate: object = None                      # yardımlaşma: gate(symbol) -> 0..1 risk çarpanı
    cash: float = 0.0
    books: dict = field(default_factory=dict)
    last: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    day: object = None

    def __post_init__(self):
        c, r = self.cfg["costs"], self.cfg["risk"]
        self.fee, self.slip, self.cooldown_bars = c["fee"], c["slippage"], r["cooldown_bars"]
        self.rm = RiskManager(r["risk_per_trade"], r["max_position_fraction"], r["max_daily_loss"],
                              r["max_drawdown"], r["min_notional"], self.fee)
        if not self.books:
            self.cash = self.start_equity
            self.books = {s: Book() for s in self.symbols}
            self.rm.peak = self.rm.day_start = self.start_equity

    # ---- yardımcılar -------------------------------------------------
    def equity(self) -> float:
        return self.cash + sum(b.qty * (self.last.get(s) or 0) for s, b in self.books.items())

    def can_open(self) -> bool:
        return self.rm.can_open() and not (self.live and kill_switch_active())

    def gate_mult(self, s) -> float:
        """Diğer botların bilgisine göre risk çarpanı. Asla 1'in üstüne çıkamaz."""
        return min(1.0, max(0.0, self.gate(s))) if self.gate else 1.0

    def _emit(self, t, kind, msg, trade=None, meta=None):
        if kind not in ("DUR", "GUN_FRENI"):          # bunları risk yöneticisi kaydeder
            self.rm.events.append((t, kind, msg))
        if self.on_event:
            self.on_event(t, kind, msg, trade, meta)

    def _risk_update(self, t):
        n = len(self.rm.events)
        self.rm.update(self.equity(), t)
        for ev in self.rm.events[n:]:
            if self.on_event:
                self.on_event(ev[0], ev[1], ev[2], None, None)

    def roll_day(self, t):
        if t.date() != self.day:
            self.day = t.date()
            self.rm.new_day(self.equity())

    # ---- alım / satım --------------------------------------------------
    # Karar (size_order) paper ve canlıda aynıdır. Kayıt (_book_buy/_book_sell) paper'da
    # tahmini, canlıda borsanın GERÇEK dolum fiyatı ve tutarıyla yapılır.
    def size_order(self, s, px, stop_dist):
        """Risk kuralına göre ne kadar alınmalı? (adet, tahmini dolum, stop) veya None."""
        fill = px * (1 + self.slip)
        stop = fill - stop_dist
        q = self.rm.size(self.equity(), fill, stop) * self.gate_mult(s)
        q = min(q, self.cash / (fill * (1 + self.fee)))    # nakitten fazla alınamaz
        if q * fill < self.rm.min_notional:
            return None
        return q, fill, stop

    def _buy(self, s, t, px, stop_dist):
        o = self.size_order(s, px, stop_dist)
        if o:
            q, fill, stop = o
            self._book_buy(s, t, q, fill, q * fill * (1 + self.fee), stop)

    def _book_buy(self, s, t, q, fill, cost, stop):
        b = self.books[s]
        self.cash -= cost
        adding = b.qty > 0
        if not adding:
            b.entry_t, b.best, b.stop, b.cost, b.units = t, fill, stop, 0.0, 0
        b.qty += q
        b.cost += cost
        b.units += 1
        b.last_add = fill
        b.stop = max(b.stop, stop)       # eklemede tüm pozisyonun stopu yukarı taşınır
        self._emit(t, "EKLEME" if adding else "ALIM",
                   f"{s} {q:.6f} @ {fill:,.2f} (birim {b.units}/{self.max_units}), stop {b.stop:,.2f}",
                   meta={"symbol": s, "price": fill})

    def _sell(self, s, t, px, why):
        b = self.books[s]
        fill = px * (1 - self.slip)
        self._book_sell(s, t, fill, b.qty * fill * (1 - self.fee), why)

    def _book_sell(self, s, t, fill, proceeds, why):
        b = self.books[s]
        self.cash += proceeds
        tr = Trade(s, b.entry_t, t, b.cost / b.qty, fill, b.qty, proceeds - b.cost, why, b.units)
        self.trades.append(tr)
        self.books[s] = Book(cooldown=self.cooldown_bars)
        self._emit(t, "SATIS", f"{s} @ {fill:,.2f} ({why}) kâr/zarar {tr.pnl:+,.2f} USDT", tr,
                   meta={"symbol": s, "price": fill, "reason": why})

    # ---- bar işleme ------------------------------------------------------
    def open_phase(self, s, t, price):
        """Önceki kapanışta alınan kararı bu fiyattan uygula."""
        self.last[s] = price
        self.roll_day(t)
        b = self.books[s]
        if b.pending == "out" and b.qty > 0:
            self._sell(s, t, price, "DUR" if self.rm.halted else "sinyal")
        elif b.pending in ("in", "add") and self.can_open():
            self._buy(s, t, price, b.pend_stop)
        b = self.books[s]
        b.pending = None

    def close_phase(self, s, t, low, close, row, check_stop=True):
        """Bar kapanışı: zarar-kes, iz süren stop, frenler ve sonraki karar.

        check_stop=False: stoplar zaten daha sık (saatlik/canlı) izleniyorsa.
        """
        b = self.books[s]
        if check_stop and b.qty > 0 and low <= b.stop:
            open_px = self.last.get(s, b.stop)
            self._sell(s, t, min(open_px, b.stop), "zarar-kes" if b.stop < b.cost / b.qty else "iz-süren stop")
        self.last[s] = close
        self._risk_update(t)
        b = self.books[s]
        add_step = getattr(row, "add_step", math.inf)
        if b.qty > 0:
            b.best = max(b.best, close)
            b.stop = max(b.stop, b.best - row.trail_dist)
            if row.exit or self.rm.halted:
                b.pending = "out"
            elif b.units < self.max_units and close >= b.last_add + add_step and self.can_open() and self.gate_mult(s) > 0:
                b.pending, b.pend_stop = "add", row.stop_dist
        else:
            b.cooldown = max(0, b.cooldown - 1)
            if row.entry and b.cooldown == 0 and self.can_open():
                if self.gate_mult(s) > 0:
                    b.pending, b.pend_stop = "in", row.stop_dist
                elif self.on_event:
                    self.on_event(t, "YARDIM", f"{s} alım sinyali var ama ekip kuralı engelledi", None, {"symbol": s})

    def check_stop_bar(self, s, t, open_px, low, close):
        """Backtest bekçisi: küçük bir barda (1s) stop değdi mi? (canlıdaki 10 sn bekçinin karşılığı)"""
        b = self.books[s]
        if b.qty > 0 and low <= b.stop:
            self._sell(s, t, min(open_px, b.stop), "zarar-kes" if b.stop < b.cost / b.qty else "iz-süren stop")
        self.last[s] = close

    # ---- habere tepki (diğer bottan gelen önemli haber) ----------------
    def tighten(self, s, t, price, dist, why) -> str | None:
        """Kendi ATR'sine göre stopu yukarı çek. Stop asla aşağı inmez."""
        b = self.books[s]
        new = price - dist
        if b.qty > 0 and new > b.stop:
            b.stop = new
            msg = f"{s} stop sıkılaştırıldı → {new:,.2f} ({why})"
            self._emit(t, "TEPKI", msg, meta={"symbol": s})
            return msg
        return None

    def early_entry(self, s, t, price, stop_dist, why) -> str | None:
        """Kendi giriş şartı canlı fiyatla sağlanıyorsa bar kapanışını beklemeden gir."""
        b = self.books[s]
        if b.qty == 0 and b.cooldown == 0 and not b.pending and self.can_open() and self.gate_mult(s) > 0:
            self._buy(s, t, price, stop_dist)
            if self.books[s].qty > 0:
                msg = f"{s} erken giriş ({why})"
                self._emit(t, "TEPKI", msg, meta={"symbol": s})
                return msg
        return None

    def check_stop(self, s, t, price):
        """Canlı bekçi: fiyat stopun altına inerse beklemeden sat."""
        self.last[s] = price
        b = self.books[s]
        if b.qty > 0 and price <= b.stop:
            self._sell(s, t, price, "zarar-kes (canlı)")
        self._risk_update(t)

    # ---- kalıcılık ------------------------------------------------------------
    def to_dict(self) -> dict:
        rm = self.rm
        return {
            "cash": self.cash, "start_equity": self.start_equity, "max_units": self.max_units,
            "day": str(self.day) if self.day else None, "last": self.last,
            "books": {s: {**asdict(b), "entry_t": str(b.entry_t) if b.entry_t else None} for s, b in self.books.items()},
            "risk": {"peak": rm.peak, "day_start": rm.day_start, "halted": rm.halted, "day_blocked": rm.day_blocked},
        }

    @classmethod
    def from_dict(cls, d, cfg, symbols, live=True, on_event=None, gate=None):
        import datetime as dt

        import pandas as pd
        books = {s: Book(**{**d["books"][s], "entry_t": pd.Timestamp(d["books"][s]["entry_t"]) if d["books"][s]["entry_t"] else None})
                 if s in d["books"] else Book() for s in symbols}
        p = cls(cfg, d["start_equity"], symbols, d["max_units"], live, on_event, gate,
                cash=d["cash"], books=books, last=d["last"],
                day=dt.date.fromisoformat(d["day"]) if d["day"] else None)
        for k, v in d["risk"].items():
            setattr(p.rm, k, v)
        return p
