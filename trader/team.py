"""Bot ekibi: Kaplumbağa ve Sörfçü ayrı botlardır, ortak bir "haber panosu" ile yardımlaşır.

Her bot panoya şunu yazar: her coin için trend görüşü, pozisyonu var mı, freni çalıştı mı.
Diğer bot bunu okuyup yeni işlem riskini AZALTABİLİR veya işlemi ENGELLEYEBİLİR.
Yardımlaşma riski asla artıramaz (engine.gate_mult en fazla 1).

Yardımlaşma kuralları (sonuçlara bakmadan belirlendi, testle seçildi):
  bagimsiz          : yardımlaşma yok
  buyuk_resim       : Kaplumbağa, Sörfçü o coinde günlük trendi yukarı görmüyorsa alım yapmaz
  risk_paylasimi    : diğer bot aynı coindeyse yeni giriş yarım riskle yapılır
  fren_dayanismasi  : diğer botun freni çalıştıysa yeni işlem açılmaz
  hepsi             : üçü birlikte
"""
from __future__ import annotations

import copy
from contextlib import closing
from datetime import datetime, timezone

import pandas as pd

from . import strategies
from .engine import Portfolio

BOT_NAMES = {"kaplumbaga": "Kaplumbağa", "sorfcu": "Sörfçü"}
STRATEGY_FN = {"turtle": strategies.turtle, "surfer": strategies.surfer}
STRATEGY_CFG = {"turtle": "turtle", "surfer": "surfer"}
BAR_LEN = {"turtle": pd.Timedelta("4h"), "surfer": pd.Timedelta("1D")}
RULES = ("bagimsiz", "buyuk_resim", "risk_paylasimi", "fren_dayanismasi", "hepsi")
STALE_SEC = 15 * 60     # canlıda bu süreden eski haber "haber yok" sayılır


def bot_cfg(cfg: dict, name: str) -> dict:
    """Ortak ayarlar + o bota özel risk ayarları."""
    b = cfg["bots"][name]
    c = copy.deepcopy(cfg)
    c["risk"]["risk_per_trade"] = b["risk_per_trade"]
    c["risk"]["max_drawdown"] = b["max_drawdown"]
    return c


def bot_capital(cfg: dict, name: str) -> float:
    return cfg["general"]["start_equity"] * cfg["bots"][name]["capital_share"]


def signals(cfg: dict, name: str, df15: pd.DataFrame) -> pd.DataFrame:
    strat = cfg["bots"][name]["strategy"]
    return STRATEGY_FN[strat](df15, cfg[STRATEGY_CFG[strat]])


class MemoryBoard:
    """Backtest için bellek içi haber panosu."""

    def __init__(self):
        self.views, self.flags = {}, {}

    def set_view(self, bot, s, trend_up, in_pos):
        self.views[(bot, s)] = {"trend_up": bool(trend_up), "in_pos": bool(in_pos)}

    def set_flags(self, bot, halted, day_blocked):
        self.flags[bot] = {"halted": bool(halted), "day_blocked": bool(day_blocked)}

    def view(self, bot, s):
        return self.views.get((bot, s))

    def flag(self, bot):
        return self.flags.get(bot)


def make_gate(rule: str, me: str, other: str, board):
    """Bu bot için yardımlaşma kapısı: coin -> risk çarpanı (0..1)."""
    def gate(s):
        m = 1.0
        if rule in ("buyuk_resim", "hepsi") and me == "kaplumbaga":
            v = board.view(other, s)
            if not v or not v["trend_up"]:          # haber yoksa: güvenli taraf = alma
                return 0.0
        if rule in ("risk_paylasimi", "hepsi"):
            v = board.view(other, s)
            if v and v["in_pos"]:
                m *= 0.5
        if rule in ("fren_dayanismasi", "hepsi"):
            f = board.flag(other)
            if f and (f["halted"] or f["day_blocked"]):
                return 0.0
        return m
    return gate


# ---- Haberleşme: önemli olayda diğer bota haber, alan KENDİ kuralıyla tepki verir ----
# Haber türleri (sonuçlara bakmadan belirlendi):
#   TREND_KIRILDI : bot kendi kuralıyla trend bitti deyip sattı (iki bot da gönderir)
#   SERT_DUSUS    : 1 saatte 3 ATR'den fazla düşüş (hızlı gözcü Kaplumbağa gönderir)
#   KIRILIM       : Kaplumbağa yeni yükselişe girdi
# Tepkiler (alan botun kendi ölçüsüyle):
#   pozisyondaysa ve haber kötüyse -> stopu kendi ATR'sinin yarısına sıkılaştır
#   KIRILIM + Sörfçü boştaysa      -> kendi dalga şartını canlı fiyatla şimdi kontrol et, sağlanıyorsa gir
ALARM_SETS = {
    "yok": frozenset(),
    "trend": frozenset({"TREND_KIRILDI"}),
    "trend_dusus": frozenset({"TREND_KIRILDI", "SERT_DUSUS"}),
    "tam": frozenset({"TREND_KIRILDI", "SERT_DUSUS", "KIRILIM"}),
}
TIGHTEN_ATR = {"turtle": 1.5, "surfer": 2.0}   # normal iz süren stopun (3 / 4 ATR) yarısı
CRASH_ATR = 3.0
ALARM_TEXT = {
    "TREND_KIRILDI": "trend kırıldı",
    "SERT_DUSUS": "sert düşüş",
    "KIRILIM": "kırılım başladı",
}


def watch_frame(df15: pd.DataFrame) -> pd.DataFrame:
    """Saatlik gözcü verisi: stop kontrolü ve sert düşüş alarmı için."""
    from . import indicators as ind
    h = ind.resample(df15, "1h")
    atr_prev = ind.atr(h, 14).shift(1)               # sadece geçmiş: geleceği görmez
    h["crash"] = (h["close"] - h["open"]) <= -CRASH_ATR * atr_prev
    return h.dropna()


def alarms_for(sender_strategy: str, kind: str, meta: dict | None, enabled) -> list:
    """Bir olaydan diğer bota gidecek haberleri üretir: [(tür, coin, fiyat)]."""
    if not meta or "symbol" not in meta:
        return []
    out = []
    if kind == "SATIS" and meta.get("reason") == "sinyal" and "TREND_KIRILDI" in enabled:
        out.append(("TREND_KIRILDI", meta["symbol"], meta["price"]))
    if kind == "ALIM" and sender_strategy == "turtle" and "KIRILIM" in enabled:
        out.append(("KIRILIM", meta["symbol"], meta["price"]))
    return out


def react(pf: Portfolio, strategy: str, cfg: dict, alarm: str, s: str, t, price: float, row, sender_label: str):
    """Haberi alan bot KENDİ kuralı ve KENDİ verisiyle karar verir. Dönen metin: ne yaptığı."""
    if row is None:
        return None
    atr = row.trail_dist / cfg[STRATEGY_CFG[strategy]]["trail_atr"]
    why = f"{sender_label}: {ALARM_TEXT[alarm]}"
    if alarm in ("TREND_KIRILDI", "SERT_DUSUS"):
        return pf.tighten(s, t, price, TIGHTEN_ATR[strategy] * atr, why)
    if alarm == "KIRILIM" and strategy == "surfer":
        fast, slow = row.breakout_level, row.trend_level        # dünkü EMA50 / EMA200
        if price > fast > slow:                                  # dalga şartı canlı fiyatla sağlanıyor
            return pf.early_entry(s, t, price, row.stop_dist, why)
    return None


ALL_ALARMS = ALARM_SETS["tam"]      # canlıda her haber gönderilir; hangisine işlemle tepki verileceği ayarda


def evaluate(pf: Portfolio, strategy: str, cfg: dict, alarm: str, s: str, t, price: float, row,
             sender_label: str, acting) -> tuple[bool, str]:
    """Canlı: haberi alan bot KENDİ kuralıyla değerlendirir. (işlem yaptı mı, gerekçe)"""
    b = pf.books[s]
    if alarm in acting:
        done = react(pf, strategy, cfg, alarm, s, t, price, row, sender_label)
        if done:
            return True, f"Uyguladım: {done}"
    coin = s.split("/")[0]
    if alarm in ("TREND_KIRILDI", "SERT_DUSUS"):
        if b.qty == 0:
            return False, f"{coin} pozisyonum yok, etkilenmiyorum. Kendi sinyalimi bekliyorum."
        if alarm in acting:
            return False, f"Stopum ({b.stop:,.2f}) zaten yeterince yakın, değiştirmedim."
        rule = ("büyük dalgalar saatlik fırtınada bitmez" if strategy == "surfer"
                else "4 saatlik kurallarım bozulmadı")
        return False, f"Kendi kuralımla değerlendirdim: {rule}. Stopum {b.stop:,.2f}, pozisyonda kalıyorum."
    if alarm == "KIRILIM" and strategy == "surfer":
        if b.qty > 0:
            return False, f"{coin} zaten dalgadayım."
        if row is None:
            return False, "Verim henüz hazır değil, kendi kapanışımı bekliyorum."
        if price > row.breakout_level > row.trend_level:
            return False, f"{coin} dalga şartım canlı fiyatla sağlanıyor; günlük kapanışta kuralımla gireceğim."
        return False, (f"{coin} dalga şartım sağlanmıyor (EMA50 {row.breakout_level:,.0f}, "
                       f"EMA200 {row.trend_level:,.0f}). Kıyıda bekliyorum.")
    return False, "Not aldım."


def run_team(cfg: dict, sigs: dict, rule: str, alarms=frozenset(), watch: dict | None = None,
             settle: tuple | None = None):
    """sigs: {bot: {symbol: signal_df}}. Olaylar zamana göre sıralanır (geleceği görmez).

    watch: {symbol: watch_frame} verilirse stoplar saatlik izlenir (canlıdaki bekçiye daha yakın)
    ve sert düşüş alarmı çalışır.
    settle: (zaman, {symbol: fiyat}) verilirse son kapanışta verilmiş ama henüz uygulanmamış
    kararlar bu fiyatla uygulanır. Canlı bot kararı bar kapanınca hemen uygular; kontrol
    raporunun da aynısını yapması gerekir.
    """
    board = MemoryBoard()
    names = list(sigs)
    strat = {n: cfg["bots"][n]["strategy"] for n in names}
    other = {n: next(o for o in names if o != n) for n in names}
    last_rows = {n: {} for n in names}
    pfs = {}

    def deliver(sender, alarm, s, t, price):
        rcv = other[sender]
        react(pfs[rcv], strat[rcv], cfg, alarm, s, t, price, last_rows[rcv].get(s), BOT_NAMES[sender])

    def handler(n):
        def on_event(t, kind, msg, trade, meta):
            for alarm, s, price in alarms_for(strat[n], kind, meta, alarms):
                deliver(n, alarm, s, t, price)
        return on_event

    for n in names:
        pfs[n] = Portfolio(bot_cfg(cfg, n), bot_capital(cfg, n), list(sigs[n]),
                           cfg["turtle"]["max_units"] if n == "kaplumbaga" else 1,
                           on_event=handler(n) if alarms else None,
                           gate=make_gate(rule, n, other[n], board))

    # Sıra aynı anda: 0 gözcü (son saatin stopları) -> 1 kapanış (kararlar) -> 2 açılış (uygulama)
    timeline = []
    for bi, n in enumerate(names):
        bar = BAR_LEN[strat[n]]
        for s, df in sigs[n].items():
            for row in df.itertuples():
                timeline.append((row.Index, 2, bi, s, row))
                timeline.append((row.Index + bar, 1, bi, s, row))
    if watch:
        for s, h in watch.items():
            for row in h.itertuples():
                timeline.append((row.Index + pd.Timedelta("1h"), 0, -1, s, row))
    timeline.sort(key=lambda x: (x[0], x[1], x[2]))

    curves = {n: ([], []) for n in names}
    total_t, total_v = [], []
    for t, phase, bi, s, row in timeline:
        if phase == 0:                       # saatlik gözcü: herkesin stopu + sert düşüş alarmı
            for n in names:
                if s in pfs[n].books:
                    pfs[n].check_stop_bar(s, t, row.open, row.low, row.close)
            if row.crash and "SERT_DUSUS" in alarms and "kaplumbaga" in names:
                deliver("kaplumbaga", "SERT_DUSUS", s, t, row.close)
        else:
            n = names[bi]
            pf = pfs[n]
            if phase == 2:
                pf.open_phase(s, t, row.open)
                v = board.view(n, s)               # trend görüşü değişmez, sadece pozisyon bilgisi
                board.set_view(n, s, v["trend_up"] if v else False, pf.books[s].qty > 0)
            else:
                pf.close_phase(s, t, row.low, row.close, row, check_stop=watch is None)
                last_rows[n][s] = row
                board.set_view(n, s, row.trend_up, pf.books[s].qty > 0)
                curves[n][0].append(t)
                curves[n][1].append(pf.equity())
            board.set_flags(n, pf.rm.halted, pf.rm.day_blocked)
        total_t.append(t)
        total_v.append(sum(p.equity() for p in pfs.values()))

    if settle:                         # bekleyen kararları uygula (canlıdaki gibi)
        t_end, prices = settle
        for pf in pfs.values():
            for s, b in pf.books.items():
                if b.pending and s in prices:
                    pf.open_phase(s, t_end, prices[s])
        total_t.append(t_end)
        total_v.append(sum(p.equity() for p in pfs.values()))

    for n, pf in pfs.items():          # test sonunda açık pozisyonları kapat (maliyetiyle)
        for s, b in pf.books.items():
            if b.qty > 0:
                pf._sell(s, total_t[-1], pf.last[s], "test-sonu")
        curves[n][0].append(total_t[-1])
        curves[n][1].append(pf.equity())
    total_v[-1] = sum(p.equity() for p in pfs.values())
    total = pd.Series(total_v, index=pd.DatetimeIndex(total_t)).groupby(level=0).last()
    per_bot = {n: (pd.Series(v, index=pd.DatetimeIndex(t)).groupby(level=0).last(), pfs[n].trades, pfs[n].rm.events)
               for n, (t, v) in curves.items()}
    return total, per_bot


class DbBoard:
    """Canlı botlar için haber panosu: state.db içindeki 'board' tablosu."""

    def __init__(self, state_module):
        self.state = state_module

    def set_view(self, bot, s, trend_up, in_pos):
        with closing(self.state.connect()) as con, con:
            con.execute("INSERT OR REPLACE INTO board VALUES (?,?,?,?,?)",
                        (bot, s, int(bool(trend_up)), int(bool(in_pos)), datetime.now(timezone.utc).isoformat()))

    def set_flags(self, bot, halted, day_blocked):
        with closing(self.state.connect()) as con, con:
            con.execute("INSERT OR REPLACE INTO bot_flags VALUES (?,?,?,?)",
                        (bot, int(bool(halted)), int(bool(day_blocked)), datetime.now(timezone.utc).isoformat()))

    def _fresh(self, ts):
        return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() < STALE_SEC

    def view(self, bot, s):
        with closing(self.state.connect()) as con:
            r = con.execute("SELECT trend_up, in_pos, ts FROM board WHERE bot=? AND symbol=?", (bot, s)).fetchone()
        if not r or not self._fresh(r[2]):
            return None
        return {"trend_up": bool(r[0]), "in_pos": bool(r[1])}

    def flag(self, bot):
        with closing(self.state.connect()) as con:
            r = con.execute("SELECT halted, day_blocked, ts FROM bot_flags WHERE bot=?", (bot,)).fetchone()
        if not r or not self._fresh(r[2]):
            return None
        return {"halted": bool(r[0]), "day_blocked": bool(r[1])}
