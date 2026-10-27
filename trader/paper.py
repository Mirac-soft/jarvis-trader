"""Paper botlar: canlı fiyatlarla, SAHTE parayla çalışır. Hiçbir borsada emir vermez.

Her bot (kaplumbaga, sorfcu) AYRI bir süreçte çalışır:
- Kendi stratejisi, kendi 500 $'ı, kendi freni, kendi durum dosyası.
- Herkese açık fiyatları anahtarsız okur (borsaya giriş yapmaz).
- Her ~10 saniyede fiyatı kontrol eder, stop altına inilirse hemen satar.
- Kendi bar süresi kapanınca (4s / 1 gün) stratejiyi çalıştırır.
- Haber panosuna görüşünü yazar, diğer botun haberini okur (team.py).
- Aynı botun aynı anda sadece bir kopyası çalışabilir (kilit dosyası).
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from datetime import datetime, timezone

import ccxt
import pandas as pd

from . import data, state, team
from . import indicators as ind
from .engine import Portfolio
from .launcher import bot_paths, try_lock
from .safety import kill_switch_active, setup_logging

TICK_SEC = 10
SNAPSHOT_SEC = 60


def now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


class PaperBot:
    def __init__(self, cfg: dict, name: str):
        if name not in cfg["bots"]:
            raise ValueError(f"bilinmeyen bot: {name}")
        self.name, self.cfg = name, cfg
        self.bcfg = team.bot_cfg(cfg, name)
        self.strategy = cfg["bots"][name]["strategy"]
        self.bar = team.BAR_LEN[self.strategy]
        self.symbols = cfg["general"]["symbols"]
        self.paths = bot_paths(name)
        self.log = setup_logging(name)
        self.ex = ccxt.binance({"enableRateLimit": True})     # anahtarsız, sadece okuma
        self.board = team.DbBoard(state)
        other = next(o for o in cfg["bots"] if o != name)
        self.other = other
        gate = team.make_gate(cfg["team"]["rule"], name, other, self.board)
        self.acting = team.ALARM_SETS[cfg["team"]["alarms"]]     # işlemle tepki verilen haberler
        self.last_rows: dict = {}                                 # kendi son kapanmış bar verisi
        self.price_hist = {s: deque() for s in self.symbols}      # sert düşüş gözcüsü (son 1 saat)
        self.atr1h: dict = {}
        self.last_crash: dict = {}
        self.info: dict = {}
        self.errors = 0
        sf = self.paths["state"]
        if sf.exists():
            saved = json.loads(sf.read_text(encoding="utf-8"))
            self.run_id = saved["run_id"]
            self.pf = Portfolio.from_dict(saved["portfolio"], self.bcfg, self.symbols, True, self.record, gate)
            self.last_bar = {s: pd.Timestamp(t) for s, t in saved["last_bar"].items()}
            self.info = saved.get("info", {})
            self.record(now(), "BASLADI", f"{team.BOT_NAMES[name]} kaldığı yerden devam ediyor")
        else:
            capital = team.bot_capital(cfg, name)
            self.run_id = state.new_run(name, "paper", f"{now().date()}..", {"baslangic_sermaye": capital,
                                        "strateji": self.strategy})
            self.pf = Portfolio(self.bcfg, capital, self.symbols,
                                cfg["turtle"]["max_units"] if self.strategy == "turtle" else 1, True, self.record, gate)
            self.last_bar = {}
            self.record(now(), "BASLADI", f"{team.BOT_NAMES[name]} başladı, sahte sermaye {capital:,.0f} USDT")

    def record(self, t, kind, msg, trade=None, meta=None):
        self.log.info("%s %s", kind, msg)
        state.add_event(self.run_id, t, kind, msg)
        if trade is not None:
            state.add_trade(self.run_id, trade)
        for alarm, s, price in team.alarms_for(self.strategy, kind, meta, team.ALL_ALARMS):
            self.send_alarm(t, alarm, s, price)

    # ---- haberleşme ------------------------------------------------------
    def send_alarm(self, t, alarm, s, price):
        state.send_message(self.name, self.other, alarm, s, price)
        self.record(t, "HABER", f"{team.BOT_NAMES[self.other]}'e haber: {s.split('/')[0]} {team.ALARM_TEXT[alarm]}"
                                f" ({price:,.2f})")

    def watch_crash(self, t, prices):
        """Hızlı gözcü (Kaplumbağa): 1 saatte 3 ATR'den sert düşüşü yakalar."""
        if self.strategy != "turtle":
            return
        for s, p in prices.items():
            hist = self.price_hist[s]
            hist.append((t, p))
            while hist and hist[0][0] < t - pd.Timedelta("65min"):
                hist.popleft()
            ref = next((px for ts, px in hist if ts <= t - pd.Timedelta("60min")), None)
            atr = self.atr1h.get(s)
            recent = self.last_crash.get(s)
            if ref and atr and p - ref <= -team.CRASH_ATR * atr and (recent is None or t - recent > pd.Timedelta("60min")):
                self.last_crash[s] = t
                self.send_alarm(t, "SERT_DUSUS", s, p)

    def read_inbox(self, t, prices):
        """Gelen haberleri KENDİ kuralımla, KENDİ güncel fiyatımla hemen değerlendir."""
        for m in state.inbox(self.name):
            s = m["symbol"]
            if s not in self.pf.books or m["kind"] not in team.ALARM_TEXT or m["sender"] not in team.BOT_NAMES:
                state.answer_message(m["id"], "Geçersiz haber: yok sayıldı.")     # bozuk/tanımsız haber botu düşüremez
                continue
            acted, why = team.evaluate(self.pf, self.strategy, self.bcfg, m["kind"], s, t, prices.get(s, m["price"]),
                                       self.last_rows.get(s), team.BOT_NAMES[m["sender"]], self.acting)
            state.answer_message(m["id"], why)
            self.record(t, "TEPKI" if acted else "DEGERLENDIRME",
                        f"{team.BOT_NAMES[m['sender']]} haberi ({s.split('/')[0]} {team.ALARM_TEXT[m['kind']]}): {why}")

    def save(self):
        sf = self.paths["state"]
        sf.parent.mkdir(parents=True, exist_ok=True)
        tmp = sf.with_suffix(".tmp")
        payload = {"run_id": self.run_id, "last_bar": {s: str(t) for s, t in self.last_bar.items()},
                   "info": self.info, "portfolio": self.pf.to_dict(), "saved": now().isoformat()}
        tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
        os.replace(tmp, sf)        # yarım yazılmış dosya oluşmaz

    def refresh_signals(self):
        """Yeni kapanmış barları işle (4s veya 1 gün)."""
        d = self.cfg["data"]
        for s in self.symbols:
            df = data.download(d["exchange"], s, d["timeframe"], d["since"])
            df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
            df = df[["open", "high", "low", "close", "volume"]].astype(float)
            sig = team.signals(self.cfg, self.name, df)
            data_end = df.index[-1] + pd.Timedelta(d["timeframe"])
            closed = sig[sig.index + self.bar <= data_end]
            if s not in self.last_bar:          # ilk çalıştırma: geçmişi tekrar oynatma
                self.last_bar[s] = closed.index[-1]
            for row in closed[closed.index > self.last_bar[s]].itertuples():
                self.pf.close_phase(s, row.Index + self.bar, row.low, row.close, row)
                self.last_bar[s] = row.Index
            last = closed.iloc[-1]
            self.last_rows[s] = last
            if self.strategy == "turtle":
                h1 = ind.resample(df, "1h")
                self.atr1h[s] = float(ind.atr(h1, 14).iloc[-1])
            self.info[s] = {
                "bar": str(closed.index[-1] + self.bar),
                "trend_yukari": bool(last["trend_up"]),
                "giris_seviyesi": float(last["breakout_level"]),
                "cikis_seviyesi": float(last["exit_level"]),
                "trend_seviyesi": float(last["trend_level"]),
            }

    def share_news(self):
        """Haber panosuna görüşünü ve fren durumunu yaz."""
        for s in self.symbols:
            if s in self.info:
                self.board.set_view(self.name, s, self.info[s]["trend_yukari"], self.pf.books[s].qty > 0)
        self.board.set_flags(self.name, self.pf.rm.halted, self.pf.rm.day_blocked)

    def tick(self, prices: dict):
        t = now()
        self.pf.roll_day(t)
        for s, p in prices.items():
            self.pf.check_stop(s, t, p)
            if self.pf.books[s].pending:          # kapanışta verilen kararı hemen uygula
                self.pf.open_phase(s, t, p)

    def snapshot(self, prices):
        rm = self.pf.rm
        detail = {"fiyat": prices, "pozisyonlar": {}, "analiz": self.info,
                  "fren": {"durdu": rm.halted, "gun_freni": rm.day_blocked, "zirve": rm.peak, "gun_baslangic": rm.day_start},
                  "acil_durdurma": kill_switch_active()}
        for s, b in self.pf.books.items():
            if b.qty > 0:
                avg = b.cost / b.qty
                detail["pozisyonlar"][s] = {"adet": b.qty, "ort_giris": avg, "stop": b.stop,
                                            "fiyat": prices.get(s), "giris_zamani": str(b.entry_t),
                                            "kar_zarar": b.qty * prices.get(s, avg) - b.cost}
        state.add_snapshot(self.run_id, now(), self.pf.equity(), self.pf.cash, detail)

    def status_text(self):
        if self.pf.rm.halted:
            return "DURDU: düşüş freni (onay gerekli)"
        if kill_switch_active():
            return "ACİL DURDURMA aktif: yeni işlem yok"
        if self.pf.rm.day_blocked:
            return "Günlük fren: bugün yeni işlem yok"
        return "Çalışıyor"

    def run(self):
        next_bar_check = pd.Timestamp(0, tz="UTC")
        last_snap = 0.0
        shutdown = self.paths["shutdown"]
        while not shutdown.exists():
            try:
                t = now()
                if t >= next_bar_check:
                    self.refresh_signals()
                    # Bir sonraki bar kapanışından 1 dk sonra tekrar bak
                    next_bar_check = t.floor(self.bar) + self.bar + pd.Timedelta("1min")
                tickers = self.ex.fetch_tickers(self.symbols)
                prices = {s: float(tickers[s]["last"]) for s in self.symbols}
                self.tick(prices)
                self.watch_crash(now(), prices)
                self.read_inbox(now(), prices)
                self.share_news()
                if time.time() - last_snap >= SNAPSHOT_SEC:
                    self.snapshot(prices)
                    last_snap = time.time()
                self.save()
                state.heartbeat(self.name, self.status_text())
                self.errors = 0
            except Exception as e:   # bağlantı hatası vb.: bot çökmez, bekler ve dener
                self.errors += 1
                self.record(now(), "HATA", f"{type(e).__name__}: {str(e)[:200]}")
                state.heartbeat(self.name, f"Hata ({self.errors}), tekrar deneniyor")
                time.sleep(min(300, 10 * 2 ** min(self.errors, 5)))
                continue
            time.sleep(TICK_SEC)
        self.record(now(), "KAPANDI", f"{team.BOT_NAMES[self.name]} kullanıcı isteğiyle kapatıldı")
        state.heartbeat(self.name, "Kapalı")


def main(cfg, name: str):
    lock = try_lock(name)      # tek kopya garantisi; süreç bitene kadar tutulur
    if lock is None:
        return
    bot_paths(name)["shutdown"].unlink(missing_ok=True)
    PaperBot(cfg, name).run()
