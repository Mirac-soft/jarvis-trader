"""JARVIS paneli için yerel sunucu (sadece bu bilgisayar: 127.0.0.1).

Güvenlik:
- Sadece 127.0.0.1'i dinler; ağdaki diğer cihazlar erişemez.
- Veritabanı salt-okunur açılır.
- Host başlığı kontrolü (DNS rebinding saldırısına karşı).
- Komutlar (POST) sadece panelin kendisinden: Origin + özel başlık şart.
  Başka bir web sitesi tarayıcın üzerinden komut gönderemez.
- Komutlar sadece güvenli yönde: acil durdurma, bot başlat/durdur.
  Panelden alım-satım emri verilemez.
- Sıkı güvenlik başlıkları (CSP): sayfa dışarıdan kod yükleyemez.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import launcher, state
from .config import load_config
from .safety import KILL_SWITCH, kill_switch_active, setup_logging

PORT = launcher.PORT
ORIGIN = f"http://127.0.0.1:{PORT}"
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
APP_DIR = Path(__file__).resolve().parent.parent / "app"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/icon.svg": ("icon.svg", "image/svg+xml"),
          "/turtle.svg": ("turtle.svg", "image/svg+xml"),
          "/surfer.svg": ("surfer.svg", "image/svg+xml")}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
BOT_LABELS = {"kaplumbaga": "Kaplumbağa", "sorfcu": "Sörfçü"}
log = setup_logging("panel")
CFG = load_config()


def q(sql, args=()):
    try:
        with state.connect_readonly() as con:
            return [dict(r) for r in con.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:      # veritabanı/tablo henüz yok
        return []


def runs():
    """Her botun en son paper çalıştırması: {bot: (run_id, summary)}"""
    out = {}
    for b in launcher.BOTS:
        mode = "live" if CFG.get("live", {}).get("enabled") else "paper"
        r = q("SELECT id, summary FROM runs WHERE mode=? AND name=? ORDER BY id DESC LIMIT 1", (mode, b))
        if r:
            out[b] = (r[0]["id"], {**json.loads(r[0]["summary"]), "_mode": mode})
    return out


def run_ids():
    return {rid: b for b, (rid, _) in runs().items()}


def api_overview(_):
    now = datetime.now(timezone.utc)
    beats = {h["bot"]: h for h in q("SELECT * FROM heartbeat")}
    bots = {}
    for b, (rid, summary) in runs().items():
        hb = beats.get(b)
        info = {"label": BOT_LABELS[b], "running": launcher.bot_running(b), "run_id": rid, "mode": summary.get("_mode"),
                "start_equity": summary.get("baslangic_sermaye"), "strategy": summary.get("strateji"),
                "risk": CFG["bots"][b]["risk_per_trade"], "brake": CFG["bots"][b]["max_drawdown"],
                "heartbeat": {**hb, "age": (now - datetime.fromisoformat(hb["ts"])).total_seconds()} if hb else None}
        snap = q("SELECT * FROM snapshots WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (rid,))
        if snap:
            s = snap[0]
            info.update(ts=s["ts"], equity=s["equity"], cash=s["cash"], detail=json.loads(s["detail"] or "{}"))
        bots[b] = info
    for b in launcher.BOTS:     # henüz hiç çalışmamış bot da görünsün
        bots.setdefault(b, {"label": BOT_LABELS[b], "running": launcher.bot_running(b)})
    return {"now": now.isoformat(), "kill_switch": kill_switch_active(), "rule": CFG["team"]["rule"],
            "alarms": CFG["team"]["alarms"], "live": bool(CFG.get("live", {}).get("enabled")), "bots": bots}


def api_equity(_):
    """Her botun ve ekibin dakikalık sermaye eğrisi."""
    per_bot = {}
    for b, (rid, _) in runs().items():
        per_bot[b] = {r["ts"][:16]: r["equity"] for r in q("SELECT ts, equity FROM snapshots WHERE run_id=? ORDER BY rowid", (rid,))}
    minutes = sorted(set().union(*[set(v) for v in per_bot.values()])) if per_bot else []
    last = {b: None for b in per_bot}
    rows = []
    for m in minutes:
        for b in per_bot:
            last[b] = per_bot[b].get(m, last[b])
        if all(v is not None for v in last.values()):
            rows.append({"ts": m + ":00Z", **last, "ekip": sum(last.values())})
    step = max(1, len(rows) // 1500)       # grafiği hafif tut
    return rows[::step] + ([rows[-1]] if rows and (len(rows) - 1) % step else [])


def _with_bot(rows):
    ids = run_ids()
    return [{**r, "bot": ids.get(r.pop("run_id"))} for r in rows]


def api_events(params):
    after = int(params.get("after", ["0"])[0] or 0)
    ids = list(run_ids())
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    if after:
        return _with_bot(q(f"SELECT rowid AS id, run_id, ts, kind, message FROM events WHERE run_id IN ({marks}) "
                           "AND rowid>? ORDER BY rowid", (*ids, after)))
    rows = q(f"SELECT rowid AS id, run_id, ts, kind, message FROM events WHERE run_id IN ({marks}) "
             "ORDER BY rowid DESC LIMIT 150", ids)
    return _with_bot(rows[::-1])


def api_trades(_):
    ids = list(run_ids())
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return _with_bot(q(f"SELECT * FROM trades WHERE run_id IN ({marks}) ORDER BY rowid DESC LIMIT 200", ids))


def api_board(_):
    return {"views": q("SELECT * FROM board"), "flags": q("SELECT * FROM bot_flags")}


def api_backtests(_):
    rows = q("SELECT name, period, summary FROM runs WHERE id IN "
             "(SELECT MAX(id) FROM runs WHERE mode='backtest' GROUP BY name, period) ORDER BY name, period")
    return [{**r, "summary": json.loads(r["summary"])} for r in rows]


def api_approvals(_):
    """Bekleyen gerçek alım onayları ve son kararlar."""
    return {"pending": q("SELECT * FROM approvals WHERE status='bekliyor' ORDER BY id"),
            "recent": q("SELECT * FROM approvals WHERE status!='bekliyor' ORDER BY id DESC LIMIT 10")}


def api_messages(_):
    return q("SELECT id, ts, sender, receiver, kind, symbol, price, handled_ts, response "
             "FROM messages ORDER BY id DESC LIMIT 30")


def api_report(_):
    r = q("SELECT body FROM reports ORDER BY rowid DESC LIMIT 1")
    return json.loads(r[0]["body"]) if r else None


GET_ROUTES = {"/api/overview": api_overview, "/api/equity": api_equity, "/api/events": api_events,
              "/api/trades": api_trades, "/api/board": api_board, "/api/backtests": api_backtests,
              "/api/report": api_report, "/api/messages": api_messages, "/api/approvals": api_approvals}
_last_report_request = [0.0]


def control(action: str, bot: str, approval_id=None) -> str:
    if action in ("approve", "reject"):
        try:
            aid = int(approval_id)
        except (TypeError, ValueError):
            raise ValueError("geçersiz onay numarası") from None
        rows = q("SELECT status, expires FROM approvals WHERE id=?", (aid,))
        if not rows or rows[0]["status"] != "bekliyor":
            raise ValueError("bu istek artık beklemede değil")
        if datetime.now(timezone.utc) > datetime.fromisoformat(rows[0]["expires"]):
            raise ValueError("isteğin süresi dolmuş")
        new = "onaylandi" if action == "approve" else "reddedildi"
        if not state.set_approval(aid, new, "panelden", only_from="bekliyor"):
            raise ValueError("istek bu arada değişti")
        return (f"Alım #{aid} ONAYLANDI: bot birkaç saniye içinde fiyatı yeniden kontrol edip uygulayacak."
                if action == "approve" else f"Alım #{aid} reddedildi.")
    if action == "kill_on":
        KILL_SWITCH.parent.mkdir(parents=True, exist_ok=True)
        KILL_SWITCH.write_text("panelden durduruldu", encoding="utf-8")
        return "Acil durdurma AÇIK: hiçbir bot yeni işlem açmayacak."
    if action == "kill_off":
        KILL_SWITCH.unlink(missing_ok=True)
        return "Acil durdurma kapatıldı: botlar risk sınırları içinde işlem açabilir."
    if action == "report_now":
        if time.time() - _last_report_request[0] < 120:
            return "Rapor zaten hazırlanıyor, biraz bekle."
        _last_report_request[0] = time.time()
        return launcher.start_report()
    if bot not in launcher.BOTS:
        raise ValueError("bilinmeyen bot")
    if action == "bot_start":
        return f"{BOT_LABELS[bot]}: " + launcher.start_bot(bot)
    if action == "bot_stop":
        return f"{BOT_LABELS[bot]}: " + launcher.stop_bot(bot)
    raise ValueError("bilinmeyen komut")


class Handler(BaseHTTPRequestHandler):
    server_version = "JARVIS"
    sys_version = ""

    def log_message(self, *args):          # konsolsuz çalışmada stderr yok
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8"))

    def _host_ok(self):
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            self._json(403, {"hata": "izin yok"})
            return False
        return True

    def do_GET(self):
        if not self._host_ok():
            return
        url = urlparse(self.path)
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            return self._send(200, (APP_DIR / name).read_bytes(), ctype)
        if url.path in GET_ROUTES:
            try:
                return self._json(200, GET_ROUTES[url.path](parse_qs(url.query)))
            except Exception as e:
                log.exception("api hatası")
                return self._json(500, {"hata": type(e).__name__})
        self._json(404, {"hata": "bulunamadı"})

    def do_POST(self):
        if not self._host_ok():
            return
        if (urlparse(self.path).path != "/api/control" or self.headers.get("Origin") != ORIGIN
                or self.headers.get("X-Jarvis") != "1"):
            return self._json(403, {"hata": "izin yok"})
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 1000)
            body = json.loads(self.rfile.read(length) or b"{}")
            msg = control(str(body.get("action", "")), str(body.get("bot", "")), body.get("id"))
            log.info("panel komutu: %s %s", body.get("action"), body.get("bot", ""))
            self._json(200, {"mesaj": msg})
        except Exception as e:
            self._json(400, {"hata": str(e)[:200]})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log.info("panel %s adresinde", ORIGIN)
    srv.serve_forever()
