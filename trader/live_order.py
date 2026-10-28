"""Denetimli GERÇEK emir: tesisat testi (kullanıcı onayıyla, küçük tutar).

Botlar bu modülü KULLANMAZ. Sadece `jarvis.py tesisat ...` komutuyla, elle çalışır.

Güvenlik duvarları:
- Sadece BTC/TRY, tek emirde en fazla 150 TL.
- İki adım: önce PLAN (kodlu özet), kullanıcı "EVET + kod" verirse UYGULA.
- Plan 5 dakika geçerli; uygulama anında fiyat plandan %1'den fazla oynadıysa iptal.
- Piyasa emri yok: fiyatı sınırlı emir (en fazla %0,2 kayma). 20 sn'de dolmazsa iptal.
- Sadece tesisat testinde alınan BTC satılabilir (hesaptaki başka BTC'ye dokunulmaz).
- Acil durdurma (DUR dosyası) açıksa hiçbir emir gönderilmez.
- Emirden önce/sonra bakiye mutabakatı; her adım kayda geçer.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone

import ccxt

from . import state
from .net import force_ipv4
from .safety import HOME, SafetyError, kill_switch_active, load_secrets, setup_logging

force_ipv4()      # API anahtarı IPv4 adresine kısıtlı

SYMBOL = "BTC/TRY"
BASE, QUOTE = "BTC", "TRY"
BUY_TRY = 120.0             # Alım tutarı: satışta da minimumun (~100 TL) üstünde kalsın diye
MAX_ORDER_TRY = 150.0       # Sabit tavan
PRICE_BAND = 0.01           # Plan ile uygulama arasında izin verilen fiyat oynaması
LIMIT_SLIP = 0.002          # Fiyatı sınırlı emir: en fazla %0,2 kötü fiyat
PLAN_TTL_SEC = 300
FILL_WAIT_SEC = 20
PLAN_FILE = HOME / "run" / "tesisat_plan.json"
STATE_FILE = HOME / "run" / "tesisat_durum.json"   # testte alınan BTC miktarı
log = setup_logging("tesisat")


def _now():
    return datetime.now(timezone.utc)


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class SupervisedTrader:
    def __init__(self, ex=None):
        if ex is None:
            sec = load_secrets()
            key, secret = sec.get("BTCTURK_API_KEY", ""), sec.get("BTCTURK_API_SECRET", "")
            if not key or not secret:
                raise SafetyError("API anahtarı yok: ~/.trader/secrets.env dosyasını doldur.")
            ex = ccxt.btcturk({"apiKey": key, "secret": secret, "enableRateLimit": True})
        self.ex = ex
        self.market = self.ex.load_markets()[SYMBOL]
        self.min_cost = float(self.market["limits"]["cost"]["min"] or 100.0)

    def __repr__(self):
        return "<SupervisedTrader>"

    # ---- okuma ---------------------------------------------------------------
    def quote(self) -> dict:
        ob = self.ex.fetch_order_book(SYMBOL, 5)
        return {"bid": float(ob["bids"][0][0]), "ask": float(ob["asks"][0][0])}

    def balances(self) -> dict:
        b = self.ex.fetch_balance()
        return {c: {"free": float(b["free"].get(c) or 0), "total": float(b["total"].get(c) or 0)} for c in (BASE, QUOTE)}

    @staticmethod
    def bought_amount() -> float:
        if STATE_FILE.exists():
            return float(json.loads(STATE_FILE.read_text(encoding="utf-8")).get("btc", 0))
        return 0.0

    # ---- 1. adım: plan ---------------------------------------------------------
    def plan(self, side: str) -> dict:
        if side not in ("buy", "sell"):
            raise SafetyError("Sadece 'al' veya 'sat'.")
        if kill_switch_active():
            raise SafetyError("Acil durdurma açık: emir hazırlanmaz.")
        q, bal = self.quote(), self.balances()
        if side == "buy":
            if self.bought_amount() > 0:
                raise SafetyError("Önceki testte alınan BTC henüz satılmadı. Önce 'sat'.")
            limit = q["ask"] * (1 + LIMIT_SLIP)
            amount = float(self.ex.amount_to_precision(SYMBOL, BUY_TRY / limit))
            ref = q["ask"]
            if bal[QUOTE]["free"] < amount * limit * 1.01:
                raise SafetyError(f"Yetersiz TL: {bal[QUOTE]['free']:.2f} TL var, ~{amount * limit * 1.01:.2f} TL gerekli.")
        else:
            mine = self.bought_amount()
            if mine <= 0:
                raise SafetyError("Satılacak test BTC'si yok (sadece tesisat testinde alınan BTC satılabilir).")
            limit = q["bid"] * (1 - LIMIT_SLIP)
            amount = float(self.ex.amount_to_precision(SYMBOL, min(mine, bal[BASE]["free"])))
            ref = q["bid"]
        limit = float(self.ex.price_to_precision(SYMBOL, limit))
        cost = amount * limit
        if cost > MAX_ORDER_TRY:
            raise SafetyError(f"Emir {cost:.2f} TL: tavan {MAX_ORDER_TRY:.0f} TL.")
        if cost < self.min_cost:
            raise SafetyError(f"Emir {cost:.2f} TL: BtcTurk minimumu {self.min_cost:.2f} TL'nin altında.")
        p = {"code": secrets.token_hex(3).upper(), "side": side, "symbol": SYMBOL, "amount": amount,
             "limit_price": limit, "ref_price": ref, "cost_try": round(cost, 2),
             "created": _now().isoformat(), "expires": _now().timestamp() + PLAN_TTL_SEC}
        _write_json(PLAN_FILE, p)
        log.info("PLAN %s", p)
        return p

    # ---- 2. adım: onaylı uygulama ------------------------------------------------
    def execute(self, code: str) -> dict:
        if kill_switch_active():
            raise SafetyError("Acil durdurma açık: emir gönderilmez.")
        if not PLAN_FILE.exists():
            raise SafetyError("Onay bekleyen plan yok.")
        p = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
        PLAN_FILE.unlink()                                    # plan tek kullanımlık
        if code.strip().upper() != p["code"]:
            raise SafetyError("Onay kodu uyuşmuyor: plan iptal edildi.")
        if _now().timestamp() > p["expires"]:
            raise SafetyError("Planın süresi doldu (5 dk): yeniden plan hazırla.")
        q = self.quote()
        now_ref = q["ask"] if p["side"] == "buy" else q["bid"]
        if abs(now_ref / p["ref_price"] - 1) > PRICE_BAND:
            raise SafetyError(f"Fiyat plandan beri %{abs(now_ref / p['ref_price'] - 1) * 100:.2f} oynadı: iptal.")
        if p["amount"] * p["limit_price"] > MAX_ORDER_TRY:     # çift kontrol
            raise SafetyError("Tavan aşımı.")

        before = self.balances()
        cid = f"jarvis{p['code'].lower()}{int(time.time())}"
        order = self.ex.create_order(SYMBOL, "limit", p["side"], p["amount"], p["limit_price"], {"clientOrderId": cid})
        oid = str(order["id"])
        log.info("EMIR GONDERILDI id=%s %s", oid, p)

        deadline = time.time() + FILL_WAIT_SEC
        still_open = True
        while time.time() < deadline:
            time.sleep(2)
            still_open = any(str(o["id"]) == oid for o in self.ex.fetch_open_orders(SYMBOL))
            if not still_open:
                break
        if still_open:
            self.ex.cancel_order(oid, SYMBOL)
            log.info("EMIR IPTAL (20 sn'de dolmadı) id=%s", oid)
            time.sleep(2)

        fills = [t for t in self.ex.fetch_my_trades(SYMBOL, limit=50) if str(t.get("order")) == oid]
        qty = sum(float(t["amount"]) for t in fills)
        spent = sum(float(t["cost"]) for t in fills)
        fees = {}
        for t in fills:
            f = t.get("fee") or {}
            if f.get("cost"):
                fees[f.get("currency")] = fees.get(f.get("currency"), 0) + float(f["cost"])
        after = self.balances()
        d_base = after[BASE]["total"] - before[BASE]["total"]
        d_quote = after[QUOTE]["total"] - before[QUOTE]["total"]

        if p["side"] == "buy":
            _write_json(STATE_FILE, {"btc": self.bought_amount() + qty, "updated": _now().isoformat()})
            expected_base = qty - fees.get(BASE, 0)
        else:
            left = max(0.0, self.bought_amount() - qty)
            if left < 1e-8:
                STATE_FILE.unlink(missing_ok=True)
            else:
                _write_json(STATE_FILE, {"btc": left, "updated": _now().isoformat()})
            expected_base = -qty
        mismatch = abs(d_base - expected_base) > max(1e-8, qty * 0.01)
        result = {"islem": "ALIM" if p["side"] == "buy" else "SATIS", "emir_id": oid, "dolan_btc": qty,
                  "ort_fiyat": spent / qty if qty else None, "tutar_try": spent, "komisyon": fees,
                  "komisyon_orani_%": (fees.get(QUOTE, 0) / spent * 100) if spent and fees.get(QUOTE) else None,
                  "iptal_edilen_kisim": still_open, "bakiye_degisimi": {BASE: d_base, QUOTE: d_quote},
                  "mutabakat": "UYUMSUZ" if mismatch else "UYUMLU"}
        log.info("SONUC %s", result)
        self._record(result)
        return result

    def _record(self, r):
        runs = [x for x in state_runs() if x["name"] == "tesisat"]
        run_id = runs[0]["id"] if runs else state.new_run("tesisat", "live", f"{_now().date()}..", {"not": "denetimli tesisat testi"})
        state.add_event(run_id, _now(), r["islem"],
                        f"GERÇEK {r['islem']} {r['dolan_btc']:.8f} BTC @ {r['ort_fiyat'] or 0:,.0f} TL, "
                        f"komisyon {r['komisyon']}, mutabakat {r['mutabakat']}")


def state_runs():
    from contextlib import closing
    with closing(state.connect()) as con:
        return [{"id": r[0], "name": r[1]} for r in con.execute("SELECT id, name FROM runs WHERE mode='live' ORDER BY id DESC")]
