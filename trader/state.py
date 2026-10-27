"""Durum deposu: botların ve testlerin sonuçları burada tutulur.

Mobil uygulama ileride SADECE bu veritabanını okuyacak (salt-okunur API).
Telefonun bota emir verme yetkisi bilerek yoktur; en fazla "DUR" isteği
eklenecek ve o da sadece işlem açmayı engeller.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from .safety import HOME

DB_PATH = HOME / "state.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    name TEXT NOT NULL,          -- strateji adı
    mode TEXT NOT NULL,          -- backtest | paper | live
    period TEXT NOT NULL,        -- örn. 2020-01-01..2023-12-31
    summary TEXT NOT NULL        -- JSON özet
);
CREATE TABLE IF NOT EXISTS trades (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    symbol TEXT, entry_time TEXT, exit_time TEXT, entry REAL, exit REAL,
    qty REAL, pnl REAL, reason TEXT, units INTEGER
);
CREATE TABLE IF NOT EXISTS snapshots (   -- canlı botun dakikalık durumu
    run_id INTEGER NOT NULL REFERENCES runs(id),
    ts TEXT NOT NULL, equity REAL NOT NULL, cash REAL NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    day TEXT NOT NULL, value REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    run_id INTEGER REFERENCES runs(id),
    ts TEXT NOT NULL, kind TEXT NOT NULL, message TEXT
);
CREATE TABLE IF NOT EXISTS heartbeat (   -- canlı botlar "yaşıyorum" der
    bot TEXT PRIMARY KEY, ts TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS board (       -- botların haber panosu: trend görüşü + pozisyon
    bot TEXT NOT NULL, symbol TEXT NOT NULL, trend_up INTEGER, in_pos INTEGER, ts TEXT NOT NULL,
    PRIMARY KEY (bot, symbol)
);
CREATE TABLE IF NOT EXISTS reports (     -- haftalık kontrol raporları
    ts TEXT NOT NULL, verdict TEXT NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_flags (   -- botların fren durumu (fren dayanışması için)
    bot TEXT PRIMARY KEY, halted INTEGER, day_blocked INTEGER, ts TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    HOME.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")      # panel okurken bot yazabilsin
    con.executescript(SCHEMA)
    return con


def connect_readonly() -> sqlite3.Connection:
    """Panel ve mobil API için: bu bağlantı veritabanına YAZAMAZ."""
    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def iso(t) -> str:
    return t.isoformat() if hasattr(t, "isoformat") else str(t)


def trade_row(run_id, t):
    return (run_id, t.symbol, iso(t.entry_time), iso(t.exit_time), t.entry, t.exit, t.qty, t.pnl, t.reason, t.units)


def new_run(name: str, mode: str, period: str, summary: dict) -> int:
    with closing(connect()) as con, con:
        return con.execute("INSERT INTO runs(created, name, mode, period, summary) VALUES (?,?,?,?,?)",
                           (datetime.now(timezone.utc).isoformat(), name, mode, period, json.dumps(summary))).lastrowid


def add_event(run_id, t, kind, message):
    with closing(connect()) as con, con:
        con.execute("INSERT INTO events VALUES (?,?,?,?)", (run_id, iso(t), kind, message))


def add_trade(run_id, trade):
    with closing(connect()) as con, con:
        con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)", trade_row(run_id, trade))


def add_snapshot(run_id, t, equity, cash, detail: dict):
    with closing(connect()) as con, con:
        con.execute("INSERT INTO snapshots VALUES (?,?,?,?,?)",
                    (run_id, iso(t), equity, cash, json.dumps(detail, default=str)))


def last_report_age_days() -> float | None:
    try:
        with closing(connect_readonly()) as con:
            r = con.execute("SELECT ts FROM reports ORDER BY rowid DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    if not r:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(r[0])).total_seconds() / 86400


def heartbeat(bot: str, status: str):
    with closing(connect()) as con, con:
        con.execute("INSERT OR REPLACE INTO heartbeat VALUES (?,?,?)",
                    (bot, datetime.now(timezone.utc).isoformat(), status))


def save_run(name: str, mode: str, period: str, summary: dict, equity, trades, events) -> int:
    with closing(connect()) as con, con:
        cur = con.execute(
            "INSERT INTO runs(created, name, mode, period, summary) VALUES (?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), name, mode, period, json.dumps(summary, default=float)),
        )
        run_id = cur.lastrowid
        con.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)", [trade_row(run_id, t) for t in trades])
        daily = equity.resample("1D").last().dropna()
        con.executemany("INSERT INTO equity VALUES (?,?,?)",
                        [(run_id, str(d.date()), float(v)) for d, v in daily.items()])
        con.executemany("INSERT INTO events VALUES (?,?,?,?)",
                        [(run_id, iso(ts), kind, msg) for ts, kind, msg in events])
        return run_id
