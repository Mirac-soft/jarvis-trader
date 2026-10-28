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


class ReadOnlyExchangeTests(unittest.TestCase):
    def _ex(self):
        from unittest import mock

        from trader import exchange
        with mock.patch.object(exchange, "load_secrets",
                               return_value={"BTCTURK_API_KEY": "k" * 40, "BTCTURK_API_SECRET": "s" * 40}):
            return exchange.ReadOnlyBtcTurk()

    def test_no_trading_or_withdraw_methods(self):
        from trader.safety import SafetyError
        ex = self._ex()
        for name in ("create_order", "create_market_buy_order", "cancel_order", "withdraw", "transfer", "_ex2"):
            with self.assertRaises(SafetyError):
                getattr(ex, name)

    def test_cannot_be_modified_and_hides_keys(self):
        from trader.safety import SafetyError
        ex = self._ex()
        with self.assertRaises(SafetyError):
            ex.create_order = lambda *a: None
        self.assertNotIn("kkkk", repr(ex))

    def test_missing_keys_refused(self):
        from unittest import mock

        from trader import exchange
        from trader.safety import SafetyError
        with mock.patch.object(exchange, "load_secrets", return_value={}):
            with self.assertRaises(SafetyError):
                exchange.ReadOnlyBtcTurk()


class FakeBtcTurk:
    """Gerçek borsa yerine: emirleri kaydeder, hemen doldurur."""

    def __init__(self, ask=4_000_000.0, bid=3_999_000.0, try_free=200.0, btc=0.0):
        self.ask, self.bid, self.try_free, self.btc = ask, bid, try_free, btc
        self.orders = []

    def load_markets(self):
        return {"BTC/TRY": {"limits": {"cost": {"min": 99.91}}}}

    def fetch_order_book(self, s, n):
        return {"bids": [[self.bid, 1]], "asks": [[self.ask, 1]]}

    def fetch_balance(self):
        return {"free": {"TRY": self.try_free, "BTC": self.btc}, "total": {"TRY": self.try_free, "BTC": self.btc}}

    def amount_to_precision(self, s, a):
        return f"{int(a * 1e8) / 1e8:.8f}"

    def price_to_precision(self, s, p):
        return f"{round(p):.0f}"

    def create_order(self, s, typ, side, amount, price, params):
        assert typ == "limit"
        self.orders.append((side, amount, price))
        cost = amount * price
        if side == "buy":
            self.try_free -= cost * 1.0009
            self.btc += amount
        else:
            self.try_free += cost * (1 - 0.0009)
            self.btc -= amount
        return {"id": str(len(self.orders))}

    def fetch_open_orders(self, s):
        return []

    def cancel_order(self, oid, s):
        raise AssertionError("iptal beklenmiyordu")

    def fetch_my_trades(self, s, limit=50):
        side, amount, price = self.orders[-1]
        return [{"order": str(len(self.orders)), "amount": amount, "cost": amount * price,
                 "fee": {"cost": amount * price * 0.0009, "currency": "TRY"}}]


class SupervisedOrderTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from trader import live_order
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(live_order, "PLAN_FILE", self.tmp / "plan.json"),
                        mock.patch.object(live_order, "STATE_FILE", self.tmp / "durum.json"),
                        mock.patch.object(live_order, "kill_switch_active", return_value=False),
                        mock.patch.object(live_order.time, "sleep", lambda s: None),
                        mock.patch.object(live_order.SupervisedTrader, "_record", lambda self, r: None),
                        mock.patch.object(live_order, "log", mock.MagicMock())]   # gerçek emir kaydını kirletme
        for p in self.patches:
            p.start()
        self.lo = live_order
        self.ex = FakeBtcTurk()
        self.tr = live_order.SupervisedTrader(ex=self.ex)

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_full_buy_then_sell_cycle(self):
        p = self.tr.plan("buy")
        self.assertLessEqual(p["cost_try"], self.lo.MAX_ORDER_TRY)
        r = self.tr.execute(p["code"])
        self.assertEqual(r["mutabakat"], "UYUMLU")
        self.assertGreater(self.tr.bought_amount(), 0)
        p2 = self.tr.plan("sell")
        self.assertAlmostEqual(p2["amount"], self.tr.bought_amount())
        self.tr.execute(p2["code"])
        self.assertEqual(self.tr.bought_amount(), 0)
        self.assertEqual([o[0] for o in self.ex.orders], ["buy", "sell"])

    def test_wrong_code_sends_nothing_and_kills_plan(self):
        from trader.safety import SafetyError
        self.tr.plan("buy")
        with self.assertRaises(SafetyError):
            self.tr.execute("YANLIS")
        self.assertEqual(self.ex.orders, [])
        self.assertFalse(self.lo.PLAN_FILE.exists())

    def test_expired_plan_refused(self):
        import json

        from trader.safety import SafetyError
        p = self.tr.plan("buy")
        p["expires"] = 0
        self.lo.PLAN_FILE.write_text(json.dumps(p))
        with self.assertRaises(SafetyError):
            self.tr.execute(p["code"])
        self.assertEqual(self.ex.orders, [])

    def test_price_move_refused(self):
        from trader.safety import SafetyError
        p = self.tr.plan("buy")
        self.ex.ask *= 1.02
        with self.assertRaises(SafetyError):
            self.tr.execute(p["code"])
        self.assertEqual(self.ex.orders, [])

    def test_kill_switch_blocks(self):
        from unittest import mock

        from trader.safety import SafetyError
        p = self.tr.plan("buy")
        with mock.patch.object(self.lo, "kill_switch_active", return_value=True):
            with self.assertRaises(SafetyError):
                self.tr.execute(p["code"])
        self.assertEqual(self.ex.orders, [])

    def test_cap_and_minimum(self):
        from unittest import mock

        from trader.safety import SafetyError
        with mock.patch.object(self.lo, "BUY_TRY", 500.0):
            self.ex.try_free = 1000
            with self.assertRaises(SafetyError):
                self.tr.plan("buy")
        with mock.patch.object(self.lo, "BUY_TRY", 50.0):
            with self.assertRaises(SafetyError):
                self.tr.plan("buy")

    def test_cannot_sell_btc_not_bought_by_test(self):
        from trader.safety import SafetyError
        self.ex.btc = 0.01                    # hesapta başka BTC var
        with self.assertRaises(SafetyError):
            self.tr.plan("sell")

    def test_sell_needs_no_code_and_works_under_kill_switch(self):
        from unittest import mock
        p = self.tr.plan("buy")
        self.tr.execute(p["code"])
        with mock.patch.object(self.lo, "kill_switch_active", return_value=True):
            r = self.tr.sell_now()                       # acil durdurma çıkışı engellemez
        self.assertEqual(r["islem"], "SATIS")
        self.assertEqual(self.tr.bought_amount(), 0)

    def test_kill_switch_blocks_buy_plan(self):
        from unittest import mock

        from trader.safety import SafetyError
        with mock.patch.object(self.lo, "kill_switch_active", return_value=True):
            with self.assertRaises(SafetyError):
                self.tr.plan("buy")

    def test_no_buy_without_approval_path(self):
        """Onaysız alım yapan bir fonksiyon olmamalı: tek alım yolu plan + kod."""
        public = {n for n in dir(self.lo.SupervisedTrader) if not n.startswith("__")}
        self.assertEqual(public, {"quote", "balances", "bought_amount", "plan", "execute", "sell_now", "_record"})

    def test_bots_cannot_use_live_orders(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "trader"
        for name in ("paper.py", "engine.py", "team.py", "dashboard.py", "launcher.py", "report.py"):
            src = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("live_order", src, name)
            self.assertNotIn("create_order", src, name)


class FakeExchange:
    """Gerçek modu test etmek için sahte borsa: emirler anında dolar, stoplar bekler."""

    def __init__(self, usdt=200.0):
        self.px = {"BTC/USDT": 80000.0, "ETH/USDT": 2400.0}
        self.bal = {"USDT": usdt, "BTC": 0.0, "ETH": 0.0}
        self.open, self.trades, self.created, self.n = {}, [], [], 0
        self.no_permission = False

    def load_markets(self):
        return {s: {"limits": {"cost": {"min": 9.91}}} for s in self.px}

    def fetch_order_book(self, s, n):
        p = self.px[s]
        return {"bids": [[p * 0.9999, 1]], "asks": [[p * 1.0001, 1]]}

    def fetch_balance(self):
        return {"total": dict(self.bal), "free": dict(self.bal)}

    def amount_to_precision(self, s, a):
        return f"{int(a * 1e8) / 1e8:.8f}"

    def price_to_precision(self, s, p):
        return f"{p:.2f}"

    def create_order(self, s, typ, side, amount, price=None, params=None):
        self.n += 1
        oid = str(self.n)
        self.created.append((typ, side, s, amount, price))
        if typ == "stopMarket":
            self.open[oid] = {"s": s, "amount": amount, "stop": (params or {})["stopPrice"]}
        else:
            self._fill(oid, s, side, amount, self.px[s])
        return {"id": oid}

    def _fill(self, oid, s, side, amount, price):
        base, cost = s.split("/")[0], amount * price
        fee = cost * 0.0024                      # komisyon + KDV
        if side == "buy":
            self.bal["USDT"] -= cost + fee
            self.bal[base] += amount
        else:
            self.bal["USDT"] += cost - fee
            self.bal[base] -= amount
        self.trades.append({"order": oid, "amount": amount, "cost": cost, "fee": {"cost": fee, "currency": "USDT"}})

    def fetch_open_orders(self, s=None):
        return [{"id": k} for k, v in self.open.items() if s is None or v["s"] == s]

    def cancel_order(self, oid, s=None):
        import ccxt
        if self.no_permission:
            raise ccxt.AuthenticationError("401")
        if str(oid) not in self.open:
            raise ccxt.OrderNotFound("yok")
        del self.open[str(oid)]

    def fetch_my_trades(self, s, limit=50):
        return list(self.trades)

    def trigger_stops(self, s, price):
        self.px[s] = price
        for oid, o in list(self.open.items()):
            if o["s"] == s and price <= o["stop"]:
                del self.open[oid]
                self._fill(oid, s, "sell", o["amount"], price)


class LiveModeTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock

        from trader import broker, live_portfolio, state
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(state, "DB_PATH", self.tmp / "state.db"),
                        mock.patch.object(broker, "LOCK_FILE", self.tmp / "hesap.lock"),
                        mock.patch.object(broker, "log", mock.MagicMock())]
        for p in self.patches:
            p.start()
        self.state = state
        self.ex = FakeExchange()
        self.br = broker.LiveBroker(CFG, ex=self.ex, sleep=lambda s: None)
        self.pf = live_portfolio.LivePortfolio(CFG, 100.0, ["BTC/USDT", "ETH/USDT"], 1, live=True,
                                               broker=self.br, bot="kaplumbaga")
        self.t = pd.Timestamp("2026-11-01", tz="UTC")
        self.row = SimpleNamespace(entry=True, exit=False, stop_dist=2400.0, trail_dist=3600.0)
        self.events = []
        self.pf.on_event = lambda t, k, m, tr, meta: self.events.append(k)

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _signal_and_approve(self):
        s = "BTC/USDT"
        self.pf.close_phase(s, self.t, 80000, 80000, self.row)
        self.pf.open_phase(s, self.t, 80000)
        a = self.state.approvals_for("kaplumbaga", "bekliyor")[0]
        self.state.set_approval(a["id"], "onaylandi", only_from="bekliyor")
        return self.state.approvals_for("kaplumbaga", "onaylandi")[0]

    def test_buy_signal_only_requests_approval(self):
        self.pf.close_phase("BTC/USDT", self.t, 80000, 80000, self.row)
        self.pf.open_phase("BTC/USDT", self.t, 80000)
        self.assertEqual(self.ex.created, [])                      # borsaya HİÇBİR emir gitmedi
        self.assertEqual(len(self.state.approvals_for("kaplumbaga", "bekliyor")), 1)
        self.assertIn("ONAY_BEKLIYOR", self.events)

    def test_approved_buy_places_exchange_stop_once(self):
        a = self._signal_and_approve()
        self.assertEqual(self.pf.execute_approved(a, self.t, 80000), "uygulandı")
        b = self.pf.books["BTC/USDT"]
        self.assertAlmostEqual(b.qty, self.ex.bal["BTC"])          # defter = borsa
        self.assertLessEqual(b.cost, a["usdt"] * 1.01)              # onaylanandan fazla değil
        self.assertTrue(b.ex_stop_id and b.ex_stop_id in self.ex.open)
        self.assertNotEqual(self.pf.execute_approved(a, self.t, 80000), "uygulandı")   # ikinci kez yok
        self.assertEqual(len([c for c in self.ex.created if c[0] == "limit"]), 1)
        # İkinci duvar: pozisyon kapansa bile aynı onay veritabanında tek kullanımlık
        self.pf.books["BTC/USDT"].qty = 0
        self.assertEqual(self.pf.execute_approved(a, self.t, 80000), "durum değişti")
        self.assertEqual(len([c for c in self.ex.created if c[0] == "limit"]), 1)

    def test_approval_cancelled_when_price_moved_expired_or_killed(self):
        from unittest import mock

        from trader import engine
        a = self._signal_and_approve()
        self.assertIn("oynamış", self.pf.execute_approved(a, self.t, 84000))
        a = self._signal_and_approve()
        a["expires"] = "2000-01-01T00:00:00+00:00"
        self.assertIn("süresi", self.pf.execute_approved(a, self.t, 80000))
        a = self._signal_and_approve()
        with mock.patch.object(engine, "kill_switch_active", return_value=True):
            self.assertIn("acil", self.pf.execute_approved(a, self.t, 80000))
        self.assertEqual(self.ex.created, [])

    def test_trailing_stop_moves_exchange_stop(self):
        self.pf.execute_approved(self._signal_and_approve(), self.t, 80000)
        old = self.pf.books["BTC/USDT"].ex_stop_id
        self.pf.books["BTC/USDT"].stop += 1000
        self.pf.sync_stop("BTC/USDT", self.t)
        new = self.pf.books["BTC/USDT"].ex_stop_id
        self.assertNotEqual(old, new)
        self.assertNotIn(old, self.ex.open)
        self.assertIn(new, self.ex.open)

    def test_exchange_stop_fill_is_recorded(self):
        self.pf.execute_approved(self._signal_and_approve(), self.t, 80000)
        self.ex.trigger_stops("BTC/USDT", 77000)                    # bilgisayar kapalıyken düştü
        self.pf.check_exchange_stop("BTC/USDT", self.t)
        self.assertEqual(self.pf.books["BTC/USDT"].qty, 0)
        self.assertEqual(self.pf.trades[-1].reason, "borsa-stop")
        self.assertAlmostEqual(self.ex.bal["BTC"], 0)

    def test_live_stop_sells_and_cancels_exchange_stop(self):
        self.pf.execute_approved(self._signal_and_approve(), self.t, 80000)
        stop = self.pf.books["BTC/USDT"].stop
        self.ex.px["BTC/USDT"] = stop - 10
        self.pf.check_stop("BTC/USDT", self.t, stop - 10)          # onaysız satış
        self.assertEqual(self.pf.books["BTC/USDT"].qty, 0)
        self.assertEqual(self.ex.open, {})                          # borsada artık stop kalmadı
        self.assertAlmostEqual(self.ex.bal["BTC"], 0)

    def test_lost_exchange_stop_is_replaced(self):
        self.pf.execute_approved(self._signal_and_approve(), self.t, 80000)
        self.ex.open.clear()                                        # kullanıcı borsada elle iptal etti
        self.pf.check_exchange_stop("BTC/USDT", self.t)
        self.assertEqual(len(self.ex.open), 1)

    def test_broker_walls(self):
        from trader.safety import SafetyError
        with self.assertRaises(SafetyError):
            self.br.buy("BTC/USDT", CFG["live"]["max_order_usdt"] + 1, 80000)    # tavan
        with self.assertRaises(SafetyError):
            self.br.buy("BTC/USDT", 5, 80000)                                     # minimum
        with self.assertRaises(SafetyError):
            self.br.buy("PEPE/USDT", 20, 1)                                       # izinsiz coin
        with self.assertRaises(SafetyError):
            self.br.buy("BTC/USDT", 20, 70000)                                    # fiyat bandı
        self.assertEqual(self.ex.created, [])

    def test_no_trade_permission_blocks_live(self):
        from trader.safety import SafetyError
        self.ex.no_permission = True
        with self.assertRaises(SafetyError):
            self.br.check_trade_permission()

    def test_live_mode_needs_both_locks_and_caps(self):
        import copy
        from unittest import mock

        from trader import safety
        cfg = copy.deepcopy(CFG)
        with self.assertRaises(safety.SafetyError):
            safety.assert_live_allowed(cfg)                         # enabled = false
        cfg["live"]["enabled"] = True
        with mock.patch.object(safety, "LIVE_APPROVAL_FILE", self.tmp / "yok"):
            with self.assertRaises(safety.SafetyError):
                safety.assert_live_allowed(cfg)                     # onay dosyası yok
        cfg["live"]["capital_usdt"] = 5000
        with self.assertRaises(safety.SafetyError):
            safety.validate_config(cfg)                             # sermaye tavanı


class SettleTests(unittest.TestCase):
    def test_pending_decision_is_settled_like_live(self):
        """Son kapanışta verilen alım kararı, raporda da canlıdaki gibi uygulanmalı."""
        from trader import team
        df = fake_prices(n=30000, seed=11)
        sigs = {n: {"BTC/USDT": team.signals(CFG, n, df)} for n in CFG["bots"]}
        sur = sigs["sorfcu"]["BTC/USDT"]
        cut = sur.index[sur["entry"].to_numpy().nonzero()[0][0]]   # Sörfçü'nün ilk alım sinyali günü
        trimmed = {n: {"BTC/USDT": s["BTC/USDT"].loc[:cut]} for n, s in sigs.items()}
        end = cut + pd.Timedelta("1D")
        _, without = team.run_team(CFG, trimmed, "bagimsiz")
        _, with_settle = team.run_team(CFG, trimmed, "bagimsiz", settle=(end, {"BTC/USDT": 100.0}))
        self.assertEqual(len([t for t in without["sorfcu"][1] if t.reason == "test-sonu"]), 0)
        self.assertEqual(len([t for t in with_settle["sorfcu"][1] if t.reason == "test-sonu"]), 1)


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
