"""JARVIS komut satırı.

Kullanım:
  python jarvis.py veri        -> geçmiş fiyat verisini indir/güncelle
  python jarvis.py test        -> stratejileri geçmiş veride test et
  python jarvis.py durum       -> güvenlik durumunu göster
  python jarvis.py uygulama    -> botu + paneli başlat, uygulama penceresini aç
  python jarvis.py ekip-test   -> Kaplumbağa + Sörfçü ekibini yardımlaşma kurallarıyla test et
  python jarvis.py bot kaplumbaga|sorfcu -> tek bir botu çalıştır
  python jarvis.py panel       -> sadece panel sunucusunu çalıştır
  python jarvis.py fren-sifirla kaplumbaga|sorfcu -> düşüş freninden sonra botu yeniden aç (onay ister)
"""
from __future__ import annotations

import sys

import pandas as pd

from trader import backtest, data, metrics, state, strategies
from trader.config import load_config
from trader.safety import HOME, KILL_SWITCH, kill_switch_active

if sys.stdout is None:          # penceresiz (pythonw) çalışmada çıktı kaybolmasın diye
    sys.stdout = sys.stderr = open(HOME / "logs" / "konsol.log", "a", encoding="utf-8")

# Geliştirme ve kontrol dönemleri: stratejiler 2020-2023'e bakılarak seçildi,
# 2024 sonrası "hiç görmediği" veri olarak saklandı (aşırı uyumu yakalamak için).
PERIODS = {
    "tum": ("2020-01-01", None),
    "gelistirme_2020_2023": ("2020-01-01", "2023-12-31"),
    "kontrol_2024_sonrasi": ("2024-01-01", None),
}
# Sonuçlara bakmadan ÖNCE belirlenen varyantlar (aşırı uyumu önlemek için sabit liste).
VARIANTS = {
    "V1_BTC": (["BTC/USDT"], 1),
    "V2_BTC_ETH": (["BTC/USDT", "ETH/USDT"], 1),
    "V3_BTC_ETH_kademeli": (["BTC/USDT", "ETH/USDT"], 4),
}


def cmd_veri(cfg):
    d = cfg["data"]
    for sym in cfg["general"]["symbols"]:
        data.download(d["exchange"], sym, d["timeframe"], d["since"])


def cmd_test(cfg):
    d, g = cfg["data"], cfg["general"]
    eq0 = g["start_equity"]
    sigs_all = {s: strategies.turtle(data.load(d["exchange"], s, d["timeframe"]), cfg["turtle"]) for s in g["symbols"]}
    rows = []
    for label, (syms, units) in VARIANTS.items():
        for pname, (start, end) in PERIODS.items():
            sigs = {s: sigs_all[s].loc[start:end] for s in syms}
            eq, trades, events = backtest.run_portfolio(sigs, cfg, eq0, units)
            s = metrics.summarize(eq, trades, eq0)
            state.save_run(label, "backtest", f"{start}..{end or 'bugun'}", s, eq, trades, events)
            s["frenler"] = len([e for e in events if e[1] in ("DUR", "GUN_FRENI")])
            rows.append({"varyant": label, "donem": pname, **s})
    for pname, (start, end) in PERIODS.items():   # Karşılaştırma: BTC al-tut
        c = sigs_all["BTC/USDT"]["close"].loc[start:end]
        bh = metrics.buy_and_hold(c, eq0, cfg["costs"]["fee"])
        rows.append({"varyant": "BTC_al_tut", "donem": pname,
                     "toplam_getiri_%": (bh.iloc[-1] / eq0 - 1) * 100,
                     "en_buyuk_dusus_%": float((bh / bh.cummax() - 1).min()) * 100})
    table = pd.DataFrame(rows).set_index(["varyant", "donem"])
    pd.set_option("display.width", 250)
    for pname in PERIODS:
        print(f"\n=== {pname} ===")
        print(table.xs(pname, level="donem").round(2).T.to_string())


def cmd_ekip_test(cfg):
    """Kaplumbağa + Sörfçü ekibini her yardımlaşma kuralıyla test eder."""
    from trader import team
    d, g = cfg["data"], cfg["general"]
    raw = {s: data.load(d["exchange"], s, d["timeframe"]) for s in g["symbols"]}
    sigs_all = {n: {s: team.signals(cfg, n, raw[s]) for s in g["symbols"]} for n in cfg["bots"]}
    eq0 = g["start_equity"]
    rows = []
    for rule in team.RULES:
        for pname, (start, end) in PERIODS.items():
            sigs = {n: {s: x.loc[start:end] for s, x in by.items()} for n, by in sigs_all.items()}
            total, per_bot = team.run_team(cfg, sigs, rule)
            trades = [t for _, tr, _ in per_bot.values() for t in tr]
            events = [e for _, _, ev in per_bot.values() for e in ev]
            s = metrics.summarize(total, trades, eq0)
            state.save_run(f"EKIP_{rule}", "backtest", f"{start}..{end or 'bugun'}", s, total, trades, events)
            for n, (eq, tr, _) in per_bot.items():
                s[f"{n}_getiri_%"] = metrics.summarize(eq, tr, team.bot_capital(cfg, n))["toplam_getiri_%"]
            s["frenler"] = len([e for e in events if e[1] == "DUR"])
            rows.append({"kural": rule, "donem": pname, **s})
    table = pd.DataFrame(rows).set_index(["kural", "donem"])
    pd.set_option("display.width", 250)
    for pname in PERIODS:
        print(f"\n=== {pname} ===")
        print(table.xs(pname, level="donem")[["toplam_getiri_%", "yillik_getiri_%", "en_buyuk_dusus_%", "sharpe",
              "islem_sayisi", "kar_faktoru", "kaplumbaga_getiri_%", "sorfcu_getiri_%", "frenler"]].round(2).to_string())


def cmd_durum(cfg):
    print(f"Mod                : {cfg['general']['mode']}  (gerçek para modu kodda kapalı)")
    print(f"Sembol             : {cfg['general']['symbol']}")
    print(f"İşlem başı risk    : %{cfg['risk']['risk_per_trade']*100:.1f}")
    print(f"Günlük zarar freni : %{cfg['risk']['max_daily_loss']*100:.1f}")
    print(f"Toplam düşüş freni : %{cfg['risk']['max_drawdown']*100:.1f}")
    print(f"Acil durdurma      : {'AKTİF' if kill_switch_active() else 'kapalı'}  ({KILL_SWITCH})")
    print(f"Gizli klasör       : {HOME}")


def cmd_bot(cfg, name=""):
    from trader import paper
    paper.main(cfg, name)


def cmd_panel(cfg):
    from trader import dashboard
    dashboard.main()


def cmd_uygulama(cfg):
    from trader import launcher
    launcher.main()


def cmd_fren_sifirla(cfg, name=""):
    """Düşüş freni çalıştıktan sonra botu yeniden açmanın TEK yolu (bilinçli insan kararı)."""
    import json

    from trader import launcher
    if name not in launcher.BOTS:
        print(f"Kullanım: python jarvis.py fren-sifirla <{'|'.join(launcher.BOTS)}>")
        return
    if launcher.bot_running(name):
        print("Önce botu durdur (panelde 'Durdur').")
        return
    sf = launcher.bot_paths(name)["state"]
    if not sf.exists():
        print("Bot durumu yok.")
        return
    if input(f"{name} freni sıfırlansın mı? Bot tekrar işlem açabilecek. (EVET yaz): ").strip() != "EVET":
        print("İptal edildi.")
        return
    d = json.loads(sf.read_text(encoding="utf-8"))
    p = d["portfolio"]
    equity = p["cash"] + sum(b["qty"] * (p["last"].get(s) or 0) for s, b in p["books"].items())
    p["risk"].update(halted=False, day_blocked=False, peak=equity, day_start=equity)
    sf.write_text(json.dumps(d), encoding="utf-8")
    print(f"Fren sıfırlandı. Yeni zirve: {equity:,.2f} USDT")


COMMANDS = {"veri": cmd_veri, "test": cmd_test, "ekip-test": cmd_ekip_test, "durum": cmd_durum, "bot": cmd_bot,
            "panel": cmd_panel, "uygulama": cmd_uygulama, "fren-sifirla": cmd_fren_sifirla}

if __name__ == "__main__":
    config = load_config()    # Güvenlik duvarı: hatalı ayarda burada durur
    command = sys.argv[1] if len(sys.argv) > 1 else "durum"
    COMMANDS[command](config, *sys.argv[2:3])
