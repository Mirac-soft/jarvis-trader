"""Canlı portföy: kararlar paper ile AYNI motorda verilir, uygulama gerçek borsada yapılır.

Kurallar:
- ALIM asla doğrudan yapılmaz: onay isteği açılır, kullanıcı panelden onaylarsa uygulanır
  (kullanıcı kuralı 2026-10-28: alımda sor, satışta sorma).
- Onaylı alım uygulanırken her şey yeniden kontrol edilir: süre, fiyat bandı, risk frenleri,
  acil durdurma, pozisyon durumu. Onaylanan tutardan fazlası asla alınmaz.
- Her alımdan hemen sonra borsaya bekleyen stop konur; stop yükseldikçe borsadaki de taşınır.
- SATIŞ onaysızdır; borsadaki stop dolmuşsa bu fark edilip kayda geçer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from . import state
from .engine import Portfolio

STOP_SYNC_MIN_MOVE = 0.002      # borsadaki stop en az %0,2 yükselince taşınır (gereksiz emir trafiği olmasın)
APPROVAL_PRICE_BAND = 0.02      # onay anındaki fiyat, istek anındakinden en fazla %2 farklı olabilir


@dataclass
class LivePortfolio(Portfolio):
    broker: object = None
    bot: str = ""
    approval_ttl: int = 3600

    # ---- ALIM: onay isteği ----------------------------------------------------
    def _buy(self, s, t, px, stop_dist):
        o = self.size_order(s, px, stop_dist)
        if not o:
            return
        q, fill, _ = o
        aid = state.request_approval(self.bot, s, q * fill, px, stop_dist, self.approval_ttl)
        if aid:
            self._emit(t, "ONAY_BEKLIYOR", f"{s} GERÇEK alım onayı bekleniyor: ~{q * fill:,.2f} USDT "
                                           f"@ ~{px:,.2f} (istek #{aid})", meta={"symbol": s})

    def execute_approved(self, a: dict, t, price: float) -> str:
        """Onaylanmış isteği uygula. Dönen metin: ne oldu."""
        s = a["symbol"]
        b = self.books[s]
        now = datetime.now(timezone.utc)
        why = None
        if now > datetime.fromisoformat(a["expires"]):
            why = "süresi dolmuş"
        elif b.qty > 0:
            why = "zaten pozisyon var"
        elif not self.can_open():
            why = "fren veya acil durdurma açık"
        elif self.gate_mult(s) <= 0:
            why = "ekip kuralı engelliyor"
        elif abs(price / a["ref_price"] - 1) > APPROVAL_PRICE_BAND:
            why = f"fiyat istekten beri %{abs(price / a['ref_price'] - 1) * 100:.1f} oynamış"
        if why:
            state.set_approval(a["id"], "iptal", why, only_from="onaylandi")
            self._emit(t, "ONAY_IPTAL", f"{s} onaylı alım uygulanmadı: {why}", meta={"symbol": s})
            return why
        o = self.size_order(s, price, a["stop_dist"])
        if not o:
            state.set_approval(a["id"], "iptal", "risk kuralı artık alım boyutuna izin vermiyor", only_from="onaylandi")
            return "boyut yetersiz"
        q, fill, _ = o
        usdt = min(q * fill, a["usdt"])                 # onaylanandan fazla ASLA
        if not state.set_approval(a["id"], "uygulandi", "", only_from="onaylandi"):
            return "durum değişti"                      # aynı onay iki kez uygulanamaz
        f = self.broker.buy(s, usdt, price)
        if not f:
            self._emit(t, "ONAY_IPTAL", f"{s} alım emri dolmadı (fiyat kaçtı)", meta={"symbol": s})
            return "dolmadı"
        self._book_buy(s, t, f.qty, f.avg, f.quote, f.avg - a["stop_dist"])
        self.sync_stop(s, t, force=True)
        return "uygulandı"

    # ---- SATIŞ: onaysız, çıkış garantili ------------------------------------------
    def _sell(self, s, t, px, why):
        b = self.books[s]
        f = self.broker.sell(s, b.qty, b.ex_stop_id)
        if not f:
            self._emit(t, "HATA", f"{s} SATIŞ YAPILAMADI ({why}) — tekrar denenecek. Gerekirse BtcTurk'ten elle sat!",
                       meta={"symbol": s})
            return
        self._book_sell(s, t, f.avg, f.quote, "borsa-stop" if f.by_stop else why)

    # ---- borsadaki stop ------------------------------------------------------------
    def sync_stop(self, s, t, force=False):
        b = self.books[s]
        if b.qty <= 0:
            return
        if force or not b.ex_stop_id or b.stop > b.ex_stop_price * (1 + STOP_SYNC_MIN_MOVE):
            res = self.broker.replace_stop(s, b.ex_stop_id, b.qty, b.stop)
            if isinstance(res, str):
                b.ex_stop_id, b.ex_stop_price = res, b.stop
            elif res is not None:                     # eski stop bu arada dolmuş
                self._book_sell(s, t, res.avg, res.quote, "borsa-stop")

    def check_exchange_stop(self, s, t):
        """Borsadaki stop kendi başına dolduysa (ör. bilgisayar kapalıyken) kayda geçir."""
        b = self.books[s]
        if b.qty > 0 and b.ex_stop_id:
            f = self.broker.stop_filled(s, b.ex_stop_id)
            if f == "kayip":                       # stop borsada yok (elle iptal vb.): hemen yeniden koy
                b.ex_stop_id = None
                self._emit(t, "HATA", f"{s} borsadaki stop kayboldu, yeniden konuyor", meta={"symbol": s})
                self.sync_stop(s, t, force=True)
            elif f:
                self._book_sell(s, t, f.avg, f.quote, "borsa-stop")
        elif b.qty > 0 and not b.ex_stop_id:      # stopsuz pozisyon olmamalı
            self.sync_stop(s, t, force=True)
