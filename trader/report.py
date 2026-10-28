"""JARVIS kontrol raporu: canlı (paper) botlar test motoruyla aynı kararları veriyor mu?

Gerçek paraya geçmeden önceki en önemli güvence. Her bot için:
1. Karar uyumu  : aynı dönemi test motorunda tekrar oynatır, işlemleri karşılaştırır.
2. Çalışma süresi: bot kaç dakika açıktı, kapalı kaldığı aralıklar (stoplar çalışmaz!).
3. Hatalar/frenler: bağlantı hataları ve çalışan frenler.
Sonuç veritabanına ve ~/.trader/raporlar/ klasörüne yazılır, panelde görünür.
"""
from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone

import pandas as pd

from . import data, state, team
from .safety import HOME

REPORT_DIR = HOME / "raporlar"
GAP_MIN = 5                 # 5 dakikadan uzun sessizlik = bot kapalıydı
PRICE_TOL = 0.01            # canlı ve test fiyatı arasında %1'e kadar fark normal (kayma)


def _q(sql, args=()):
    with closing(state.connect()) as con:
        con.row_factory = __import__("sqlite3").Row
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def _latest_runs():
    out = {}
    for b in team.BOT_NAMES:
        r = _q("SELECT id, created, summary FROM runs WHERE mode='paper' AND name=? ORDER BY id DESC LIMIT 1", (b,))
        if r:
            out[b] = r[0]
    return out


def _uptime(run_id, start, now):
    ts = [pd.Timestamp(r["ts"]) for r in _q("SELECT ts FROM snapshots WHERE run_id=? ORDER BY rowid", (run_id,))]
    total = (now - start).total_seconds() / 60
    if not ts:
        return 0.0, [(start, now)]
    points = [start, *ts, now]
    gaps = [(a, b) for a, b in zip(points, points[1:]) if (b - a).total_seconds() / 60 > GAP_MIN]
    down = sum((b - a).total_seconds() / 60 for a, b in gaps)
    return max(0.0, 100 * (1 - down / total)) if total > 0 else 100.0, gaps


def _match(live, sim, bar):
    """Canlı ve test işlemlerini coin + giriş zamanına (±1 bar) göre eşleştir."""
    issues, matched, used = [], 0, set()
    for lt in live:
        cand = [i for i, st in enumerate(sim) if i not in used and st["symbol"] == lt["symbol"]
                and abs(pd.Timestamp(st["entry_time"]) - pd.Timestamp(lt["entry_time"])) <= bar + pd.Timedelta("15min")]
        if not cand:
            issues.append(f"Canlıda olan ama testte olmayan işlem: {lt['symbol']} giriş {lt['entry_time'][:16]}")
            continue
        st = sim[cand[0]]
        used.add(cand[0])
        matched += 1
        for k, name in (("entry", "giriş"), ("exit", "çıkış")):
            diff = abs(lt[k] / st[k] - 1)
            if diff > PRICE_TOL:
                issues.append(f"{lt['symbol']} {name} fiyatı farkı %{diff*100:.2f} (canlı {lt[k]:,.2f}, test {st[k]:,.2f})")
    for i, st in enumerate(sim):
        if i not in used:
            issues.append(f"Testte olan ama canlıda olmayan işlem: {st['symbol']} giriş {str(st['entry_time'])[:16]}")
    return matched, issues


def build(cfg: dict) -> dict:
    now = pd.Timestamp(datetime.now(timezone.utc))
    runs = _latest_runs()
    if not runs:
        return {"tarih": now.isoformat(), "karar": "VERİ YOK", "botlar": {}, "ozet": ["Henüz çalışmış bir paper bot yok."]}

    d = cfg["data"]
    raw = {}
    for s in cfg["general"]["symbols"]:
        df = data.load(d["exchange"], s, d["timeframe"])
        raw[s] = df
    data_end = min(df.index[-1] for df in raw.values()) + pd.Timedelta(d["timeframe"])

    # Aynı dönemi test motorunda tekrar oynat (ekip kuralı dahil)
    sigs = {}
    for b, r in runs.items():
        bar = team.BAR_LEN[cfg["bots"][b]["strategy"]]
        created = pd.Timestamp(r["created"])
        sigs[b] = {}
        for s, df in raw.items():
            sig = team.signals(cfg, b, df)
            sig = sig[(sig.index + bar > created) & (sig.index + bar <= data_end)]   # sadece bot açıldıktan sonra kapanan barlar
            sigs[b][s] = sig
    sim_trades, sim_open = {b: [] for b in runs}, {b: [] for b in runs}
    if all(len(x) for by in sigs.values() for x in by.values()):
        first = min(pd.Timestamp(r["created"]) for r in runs.values())
        watch = {s: w[(w.index + pd.Timedelta("1h") > first) & (w.index + pd.Timedelta("1h") <= data_end)]
                 for s, w in ((s, team.watch_frame(df)) for s, df in raw.items())}
        settle = (data_end, {s: float(df["close"].iloc[-1]) for s, df in raw.items()})
        _, per_bot = team.run_team(cfg, sigs, cfg["team"]["rule"], team.ALARM_SETS[cfg["team"]["alarms"]],
                                   watch, settle)
        for b, (_, trades, _) in per_bot.items():
            for t in trades:
                row = {"symbol": t.symbol, "entry_time": str(t.entry_time), "exit_time": str(t.exit_time),
                       "entry": t.entry, "exit": t.exit, "pnl": t.pnl}
                (sim_open if t.reason == "test-sonu" else sim_trades)[b].append(row)

    bots, verdicts, summary = {}, [], []
    for b, r in runs.items():
        label, bar = team.BOT_NAMES[b], team.BAR_LEN[cfg["bots"][b]["strategy"]]
        start = pd.Timestamp(r["created"])
        live = _q("SELECT symbol, entry_time, exit_time, entry, exit, pnl, reason FROM trades WHERE run_id=?", (r["id"],))
        snap = _q("SELECT detail, equity FROM snapshots WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (r["id"],))
        live_open = sorted(json.loads(snap[0]["detail"]).get("pozisyonlar", {})) if snap else []
        sim_open_syms = sorted(t["symbol"] for t in sim_open[b])
        matched, issues = _match(live, sim_trades[b], bar)
        if live_open != sim_open_syms:
            issues.append(f"Açık pozisyonlar farklı: canlı {live_open or 'yok'}, test {sim_open_syms or 'yok'}")
        up, gaps = _uptime(r["id"], start, now)
        week_ago = (now - pd.Timedelta("7D")).isoformat()
        errors = _q("SELECT COUNT(*) AS n FROM events WHERE run_id=? AND kind='HATA' AND ts>?", (r["id"], week_ago))[0]["n"]
        brakes = _q("SELECT ts, kind, message FROM events WHERE run_id=? AND kind IN ('DUR','GUN_FRENI')", (r["id"],))
        long_gaps = [(a, b2) for a, b2 in gaps if (b2 - a) > pd.Timedelta("15min")]

        verdict = "UYUMLU"
        if issues:
            verdict = "FARK VAR"
        elif up < 95 or errors > 20:
            verdict = "UYARI"
        verdicts.append(verdict)
        bots[b] = {
            "ad": label, "karar": verdict, "baslangic": start.isoformat(), "gun": round((now - start).total_seconds() / 86400, 1),
            "sermaye": snap[0]["equity"] if snap else None,
            "canli_islem": len(live), "test_islem": len(sim_trades[b]), "eslesen": matched,
            "canli_acik": live_open, "test_acik": sim_open_syms, "farklar": issues,
            "calisma_%": round(up, 1), "kapali_araliklar": [f"{a:%d.%m %H:%M} → {b2:%d.%m %H:%M} UTC" for a, b2 in long_gaps[-10:]],
            "hata_7gun": errors, "frenler": [f"{x['ts'][:16]} {x['kind']}: {x['message']}" for x in brakes],
        }
        summary.append(f"{label}: {verdict}. {len(live)} canlı işlem, test {len(sim_trades[b])} bekliyordu; "
                       f"çalışma süresi %{up:.1f}; son 7 günde {errors} bağlantı hatası.")

    overall = "FARK VAR" if "FARK VAR" in verdicts else "UYARI" if "UYARI" in verdicts else "UYUMLU"
    if overall == "UYUMLU":
        summary.append("Canlı botlar test motoruyla aynı kararları veriyor. Sistem tutarlı.")
    elif overall == "UYARI":
        summary.append("Kararlar uyumlu ama botlar bir süre kapalı kaldı veya hata aldı. Kapalıyken stoplar çalışmaz.")
    else:
        summary.append("Canlı ile test arasında fark var: gerçek paraya geçmeden incelenmeli.")
    return {"tarih": now.isoformat(), "karar": overall, "botlar": bots, "ozet": summary}


def to_markdown(rep: dict) -> str:
    lines = [f"# JARVIS Kontrol Raporu — {rep['tarih'][:16].replace('T', ' ')} UTC", "",
             f"**Genel sonuç: {rep['karar']}**", "", *[f"- {s}" for s in rep["ozet"]], ""]
    for b in rep["botlar"].values():
        lines += [f"## {b['ad']} — {b['karar']}", "",
                  f"- Çalışma: {b['gun']} gün, açık kalma %{b['calisma_%']}",
                  f"- Sermaye: {b['sermaye']:,.2f} $" if b["sermaye"] is not None else "- Sermaye: —",
                  f"- İşlemler: canlı {b['canli_islem']}, test {b['test_islem']}, eşleşen {b['eslesen']}",
                  f"- Açık pozisyon: canlı {b['canli_acik'] or 'yok'}, test {b['test_acik'] or 'yok'}",
                  f"- Son 7 gün bağlantı hatası: {b['hata_7gun']}"]
        lines += [f"- Fark: {x}" for x in b["farklar"]]
        lines += [f"- Kapalı aralık: {x}" for x in b["kapali_araliklar"]]
        lines += [f"- Fren: {x}" for x in b["frenler"]]
        lines.append("")
    return "\n".join(lines)


def run(cfg: dict) -> dict:
    rep = build(cfg)
    with closing(state.connect()) as con, con:
        con.execute("INSERT INTO reports(ts, verdict, body) VALUES (?,?,?)",
                    (rep["tarih"], rep["karar"], json.dumps(rep, ensure_ascii=False, default=str)))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"rapor_{rep['tarih'][:10]}.md").write_text(to_markdown(rep), encoding="utf-8")
    return rep
