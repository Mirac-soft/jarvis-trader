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


def run_team(cfg: dict, sigs: dict, rule: str):
    """sigs: {bot: {symbol: signal_df}}. Barlar KAPANIŞ zamanına göre sıralanır (geleceği görmez)."""
    board = MemoryBoard()
    names = list(sigs)
    pfs = {}
    for n in names:
        other = next(o for o in names if o != n)
        pfs[n] = Portfolio(bot_cfg(cfg, n), bot_capital(cfg, n), list(sigs[n]),
                           cfg["turtle"]["max_units"] if n == "kaplumbaga" else 1,
                           gate=make_gate(rule, n, other, board))

    timeline = []
    for bi, n in enumerate(names):
        bar = BAR_LEN[cfg["bots"][n]["strategy"]]
        for s, df in sigs[n].items():
            for row in df.itertuples():
                timeline.append((row.Index, 1, bi, s, row))            # açılış
                timeline.append((row.Index + bar, 0, bi, s, row))      # kapanış (önce işlenir)
    timeline.sort(key=lambda x: (x[0], x[1], x[2]))

    curves = {n: ([], []) for n in names}
    total_t, total_v = [], []
    for t, phase, bi, s, row in timeline:
        n = names[bi]
        pf = pfs[n]
        if phase == 1:
            pf.open_phase(s, t, row.open)
            v = board.view(n, s)               # trend görüşü değişmez, sadece pozisyon bilgisi
            board.set_view(n, s, v["trend_up"] if v else False, pf.books[s].qty > 0)
        else:
            pf.close_phase(s, t, row.low, row.close, row)
            board.set_view(n, s, row.trend_up, pf.books[s].qty > 0)
            curves[n][0].append(t)
            curves[n][1].append(pf.equity())
        board.set_flags(n, pf.rm.halted, pf.rm.day_blocked)
        total_t.append(t)
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
