"""Güvenlik duvarı ve backtest motoru testleri.

Çalıştırma: python -m unittest discover tests
"""
import copy
import unittest

import numpy as np
import pandas as pd

from trader import backtest, strategies
from trader.config import load_config
from trader.risk import RiskManager
from trader.safety import SafetyError, assert_outside_onedrive, validate_config

CFG = load_config()


def fake_prices(n=6000, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, n)))
    idx = pd.date_range("2022-01-01", periods=n, freq="15min", tz="UTC")
    o = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, close) * 1.002,
                         "low": np.minimum(o, close) * 0.998, "close": close, "volume": 1.0}, index=idx)


class SafetyTests(unittest.TestCase):
    def bad(self, section, key, value):
        cfg = copy.deepcopy(CFG)
        cfg[section][key] = value
        with self.assertRaises(SafetyError):
            validate_config(cfg)

    def test_live_mode_blocked(self):
        self.bad("general", "mode", "live")

    def test_unknown_symbol_blocked(self):
        self.bad("general", "symbol", "PEPE/USDT")

    def test_risk_caps(self):
        self.bad("risk", "risk_per_trade", 0.05)
        self.bad("risk", "max_position_fraction", 2.0)   # kaldıraç
        self.bad("risk", "max_drawdown", 0.9)

    def test_unrealistic_fees_blocked(self):
        self.bad("costs", "fee", 0.0)

    def test_secrets_not_in_onedrive(self):
        from pathlib import Path
        with self.assertRaises(SafetyError):
            assert_outside_onedrive(Path.home() / "OneDrive" / "secrets.env")

    def test_position_never_exceeds_equity(self):
        rm = RiskManager(0.02, 1.0, 0.05, 0.3, 10, 0.002)
        qty = rm.size(1000, entry=100, stop=99.99)       # çok dar stop -> dev pozisyon isterdi
        self.assertLessEqual(qty * 100 * 1.002, 1000 + 1e-9)


class EngineTests(unittest.TestCase):
    def test_no_lookahead(self):
        """Gelecek fiyatları değiştirmek geçmiş sinyalleri değiştirmemeli."""
        df = fake_prices()
        cut = df.index[4000]
        a = strategies.pullback(df, CFG["pullback"]).loc[:cut]
        df2 = df.copy()
        df2.loc[df2.index > cut, ["open", "high", "low", "close"]] *= 3
        b = strategies.pullback(df2, CFG["pullback"]).loc[:cut]
        pd.testing.assert_series_equal(a["entry"], b["entry"])
        pd.testing.assert_series_equal(a["stop_dist"], b["stop_dist"])

    def test_turtle_no_lookahead(self):
        df = fake_prices()
        cut = df.index[4000]
        a = strategies.turtle(df, CFG["turtle"]).loc[:cut - pd.Timedelta("4h")]
        df2 = df.copy()
        df2.loc[df2.index > cut, ["open", "high", "low", "close"]] *= 3
        b = strategies.turtle(df2, CFG["turtle"]).loc[:cut - pd.Timedelta("4h")]
        pd.testing.assert_series_equal(a["entry"], b["entry"])

    def test_costs_make_random_trading_lose(self):
        """Rastgele al-sat komisyon yüzünden para kaybettirmeli (motor maliyeti gerçekten kesiyor mu?)."""
        df = fake_prices(seed=7)
        df["close"] = df["open"]                         # trend yok
        df["high"], df["low"] = df["open"] * 1.001, df["open"] * 0.999
        sig = df.copy()
        sig["entry"] = np.arange(len(df)) % 10 == 0
        sig["exit"] = np.arange(len(df)) % 10 == 5
        sig["stop_dist"], sig["trail_dist"] = df["close"] * 0.05, df["close"] * 0.05
        eq, trades, _ = backtest.run(sig, CFG, 1000)
        self.assertGreater(len(trades), 50)
        self.assertLess(eq.iloc[-1], 1000)


class LiveEngineTests(unittest.TestCase):
    def _row(self, entry=False, exit=False):
        from types import SimpleNamespace
        return SimpleNamespace(entry=entry, exit=exit, stop_dist=5.0, trail_dist=8.0)

    def test_state_survives_restart(self):
        """Bot kapanıp açılınca pozisyon, stop ve frenler aynen geri gelmeli."""
        from trader.engine import Portfolio
        t = pd.Timestamp("2026-01-01", tz="UTC")
        pf = Portfolio(CFG, 1000, ["BTC/USDT"], 1, live=False)
        pf.close_phase("BTC/USDT", t, 99, 100, self._row(entry=True))
        pf.open_phase("BTC/USDT", t, 100)
        pf.rm.halted = True
        pf2 = Portfolio.from_dict(pf.to_dict(), CFG, ["BTC/USDT"], live=False)
        b1, b2 = pf.books["BTC/USDT"], pf2.books["BTC/USDT"]
        self.assertGreater(b2.qty, 0)
        self.assertEqual((b1.qty, b1.stop, b1.cost), (b2.qty, b2.stop, b2.cost))
        self.assertTrue(pf2.rm.halted)
        self.assertAlmostEqual(pf.equity(), pf2.equity())

    def test_kill_switch_blocks_buys(self):
        from unittest import mock

        from trader import engine
        t = pd.Timestamp("2026-01-01", tz="UTC")
        pf = engine.Portfolio(CFG, 1000, ["BTC/USDT"], 1, live=True)
        with mock.patch.object(engine, "kill_switch_active", return_value=True):
            pf.close_phase("BTC/USDT", t, 99, 100, self._row(entry=True))
            pf.open_phase("BTC/USDT", t, 100)
        self.assertEqual(pf.books["BTC/USDT"].qty, 0)

    def test_live_stop_sells(self):
        from trader.engine import Portfolio
        t = pd.Timestamp("2026-01-01", tz="UTC")
        pf = Portfolio(CFG, 1000, ["BTC/USDT"], 1, live=False)
        pf.close_phase("BTC/USDT", t, 99, 100, self._row(entry=True))
        pf.open_phase("BTC/USDT", t, 100)
        stop = pf.books["BTC/USDT"].stop
        pf.check_stop("BTC/USDT", t, stop - 0.01)
        self.assertEqual(pf.books["BTC/USDT"].qty, 0)
        self.assertEqual(len(pf.trades), 1)


class TeamTests(unittest.TestCase):
    def test_surfer_no_lookahead(self):
        df = fake_prices(n=30000)
        cut = df.index[25000]
        a = strategies.surfer(df, CFG["surfer"]).loc[:cut - pd.Timedelta("1D")]
        df2 = df.copy()
        df2.loc[df2.index > cut, ["open", "high", "low", "close"]] *= 3
        b = strategies.surfer(df2, CFG["surfer"]).loc[:cut - pd.Timedelta("1D")]
        pd.testing.assert_series_equal(a["entry"], b["entry"])

    def test_cooperation_can_never_increase_risk(self):
        from trader.engine import Portfolio
        pf = Portfolio(CFG, 1000, ["BTC/USDT"], gate=lambda s: 5.0)
        self.assertEqual(pf.gate_mult("BTC/USDT"), 1.0)

    def test_gate_rules(self):
        from trader import team
        board = team.MemoryBoard()
        big = team.make_gate("buyuk_resim", "kaplumbaga", "sorfcu", board)
        self.assertEqual(big("BTC/USDT"), 0.0)                 # haber yok -> alma
        board.set_view("sorfcu", "BTC/USDT", True, False)
        self.assertEqual(big("BTC/USDT"), 1.0)
        brake = team.make_gate("fren_dayanismasi", "sorfcu", "kaplumbaga", board)
        self.assertEqual(brake("BTC/USDT"), 1.0)
        board.set_flags("kaplumbaga", halted=False, day_blocked=True)
        self.assertEqual(brake("BTC/USDT"), 0.0)                # diğer botun freni -> yeni işlem yok
        share = team.make_gate("risk_paylasimi", "kaplumbaga", "sorfcu", board)
        board.set_view("sorfcu", "BTC/USDT", True, True)
        self.assertEqual(share("BTC/USDT"), 0.5)

    def test_team_independent_matches_solo(self):
        """Yardımlaşmasız ekip testi, botları tek tek çalıştırmakla aynı sonucu vermeli."""
        from trader import team
        df = fake_prices(n=30000, seed=3)
        sigs = {n: {"BTC/USDT": team.signals(CFG, n, df)} for n in CFG["bots"]}
        _, per_bot = team.run_team(CFG, sigs, "bagimsiz")
        for n in CFG["bots"]:
            eq, _, _ = backtest.run_portfolio(sigs[n], team.bot_cfg(CFG, n), team.bot_capital(CFG, n))
            self.assertAlmostEqual(per_bot[n][0].iloc[-1], eq.iloc[-1], places=6)


class MessagingTests(unittest.TestCase):
    def _pf_with_position(self):
        from types import SimpleNamespace

        from trader.engine import Portfolio
        t = pd.Timestamp("2026-01-01", tz="UTC")
        pf = Portfolio(CFG, 1000, ["BTC/USDT"])
        pf.close_phase("BTC/USDT", t, 99, 100, SimpleNamespace(entry=True, exit=False, stop_dist=10.0, trail_dist=40.0))
        pf.open_phase("BTC/USDT", t, 100)
        return pf, t

    def test_tighten_only_moves_stop_up(self):
        pf, t = self._pf_with_position()
        old = pf.books["BTC/USDT"].stop
        self.assertIsNone(pf.tighten("BTC/USDT", t, 50, 5, "test"))      # aşağı çekmeye çalış
        self.assertEqual(pf.books["BTC/USDT"].stop, old)
        self.assertIsNotNone(pf.tighten("BTC/USDT", t, 100, 2, "test"))
        self.assertAlmostEqual(pf.books["BTC/USDT"].stop, 98)

    def test_receiver_decides_with_own_rules(self):
        """Haber 'acting' listesinde değilse alan bot işlem yapmaz, sadece gerekçe yazar."""
        from types import SimpleNamespace

        from trader import team
        pf, t = self._pf_with_position()
        # Sörfçü ATR = 8 / 4 = 2 -> sıkılaştırma mesafesi 2 x 2 = 4 -> yeni stop 96 (eski ~90)
        row = SimpleNamespace(trail_dist=8.0, stop_dist=6.0, breakout_level=95.0, trend_level=90.0)
        stop = pf.books["BTC/USDT"].stop
        acted, why = team.evaluate(pf, "surfer", CFG, "SERT_DUSUS", "BTC/USDT", t, 100, row, "Kaplumbağa",
                                   team.ALARM_SETS["trend"])
        self.assertFalse(acted)
        self.assertEqual(pf.books["BTC/USDT"].stop, stop)
        self.assertIn("pozisyonda kalıyorum", why)
        acted, why = team.evaluate(pf, "surfer", CFG, "TREND_KIRILDI", "BTC/USDT", t, 100, row, "Kaplumbağa",
                                   team.ALARM_SETS["trend"])
        self.assertTrue(acted)
        self.assertGreater(pf.books["BTC/USDT"].stop, stop)

    def test_alarms_only_from_important_events(self):
        from trader import team
        allx = team.ALL_ALARMS
        self.assertEqual(team.alarms_for("turtle", "SATIS", {"symbol": "BTC/USDT", "price": 1, "reason": "zarar-kes"}, allx), [])
        self.assertEqual(len(team.alarms_for("surfer", "SATIS", {"symbol": "BTC/USDT", "price": 1, "reason": "sinyal"}, allx)), 1)
        self.assertEqual(team.alarms_for("surfer", "ALIM", {"symbol": "BTC/USDT", "price": 1}, allx), [])
        self.assertEqual(len(team.alarms_for("turtle", "ALIM", {"symbol": "BTC/USDT", "price": 1}, allx)), 1)

    def test_backtest_with_alarms_no_lookahead(self):
        """Haberleşmeli ekip testi: geleceği değiştirmek geçmiş sermayeyi değiştirmemeli."""
        from trader import team
        df = fake_prices(n=30000, seed=5)
        cut = df.index[22000]
        def run(d):
            sigs = {n: {"BTC/USDT": team.signals(CFG, n, d)} for n in CFG["bots"]}
            watch = {"BTC/USDT": team.watch_frame(d)}
            total, _ = team.run_team(CFG, sigs, "fren_dayanismasi", team.ALL_ALARMS, watch)
            return total.loc[:cut - pd.Timedelta("2D")]
        a = run(df)
        df2 = df.copy()
        df2.loc[df2.index > cut, ["open", "high", "low", "close"]] *= 0.5
        b = run(df2)
        pd.testing.assert_series_equal(a, b)


class ReportTests(unittest.TestCase):
    BAR = pd.Timedelta("4h")

    def t(self, sym, entry_time, entry=100.0, exit=110.0):
        return {"symbol": sym, "entry_time": entry_time, "exit_time": entry_time, "entry": entry, "exit": exit}

    def test_matching_trades_pass(self):
        from trader.report import _match
        live = [self.t("BTC/USDT", "2026-01-01T04:01:00+00:00", 100.3)]   # 1 dk gecikme, %0,3 kayma
        sim = [self.t("BTC/USDT", "2026-01-01 04:00:00+00:00", 100.0)]
        matched, issues = _match(live, sim, self.BAR)
        self.assertEqual((matched, issues), (1, []))

    def test_missing_and_extra_trades_caught(self):
        from trader.report import _match
        live = [self.t("ETH/USDT", "2026-01-01T04:01:00+00:00")]
        sim = [self.t("BTC/USDT", "2026-01-01 04:00:00+00:00")]
        matched, issues = _match(live, sim, self.BAR)
        self.assertEqual(matched, 0)
        self.assertEqual(len(issues), 2)          # biri canlıda fazla, biri testte fazla

    def test_price_mismatch_caught(self):
        from trader.report import _match
        live = [self.t("BTC/USDT", "2026-01-01T04:01:00+00:00", 103.0)]   # %3 fark
        sim = [self.t("BTC/USDT", "2026-01-01 04:00:00+00:00", 100.0)]
        _, issues = _match(live, sim, self.BAR)
        self.assertTrue(any("fiyatı farkı" in i for i in issues))

    def test_downtime_detected(self):
        from unittest import mock

        from trader import report
        start = pd.Timestamp("2026-01-01", tz="UTC")
        snaps = [{"ts": (start + pd.Timedelta(minutes=m)).isoformat()} for m in list(range(0, 60)) + list(range(180, 240))]
        with mock.patch.object(report, "_q", return_value=snaps):
            up, gaps = report._uptime(1, start, start + pd.Timedelta(minutes=240))
        self.assertAlmostEqual(up, 50.4, delta=1)      # 2 saatlik kesinti yakalandı
        self.assertEqual(len(gaps), 1)


if __name__ == "__main__":
    unittest.main()
