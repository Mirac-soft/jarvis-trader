"use strict";
// JARVIS paneli: Kaplumbağa ve Sörfçü. Tüm metinler textContent ile yazılır (HTML enjeksiyonu yok).

const $ = (id) => document.getElementById(id);
const BOTS = {
  kaplumbaga: { label: "Kaplumbağa", icon: "/turtle.svg", color: "#22c55e" },
  sorfcu: { label: "Sörfçü", icon: "/surfer.svg", color: "#38bdf8" },
};
const RULE_NAMES = { bagimsiz: "bağımsız", buyuk_resim: "büyük resim", risk_paylasimi: "risk paylaşımı",
                     fren_dayanismasi: "fren dayanışması", hepsi: "tüm kurallar" };
const NOTIFY_KINDS = new Set(["ALIM", "EKLEME", "SATIS", "DUR", "GUN_FRENI", "HATA", "KAPANDI", "YARDIM", "HABER", "TEPKI"]);
const ALARM_NAMES = { TREND_KIRILDI: "trend kırıldı", SERT_DUSUS: "sert düşüş", KIRILIM: "kırılım başladı" };
const ALARMS_ACTING = { yok: [], trend: ["TREND_KIRILDI"], trend_dusus: ["TREND_KIRILDI", "SERT_DUSUS"],
                        tam: ["TREND_KIRILDI", "SERT_DUSUS", "KIRILIM"] };
const state = { lastEventId: 0, firstEvents: true, overview: null, equity: [] };

const num = (n, d = 2) => n == null || isNaN(n) ? "—" : Number(n).toLocaleString("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d });
const usd = (n, d = 2) => n == null || isNaN(n) ? "—" : `${num(n, d)} $`;
const pct = (n) => n == null || isNaN(n) ? "—" : `${n >= 0 ? "+" : ""}${num(n, 2)}%`;
const cls = (n) => (n > 0 ? "up" : n < 0 ? "down" : "");
const time = (ts) => new Date(ts).toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const short = (s) => (s || "").split("/")[0];

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}
function img(src, className, alt = "") { const i = el("img", className); i.src = src; i.alt = alt; return i; }

async function api(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}
async function control(action, bot = "") {
  const r = await fetch("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Jarvis": "1" },
    body: JSON.stringify({ action, bot }),
  });
  const j = await r.json();
  toast(j.mesaj || j.hata || "Tamam");
  setTimeout(refreshOverview, 800);
}
function toast(text, color = "") {
  const t = el("div", "toast", text);
  if (color) t.style.borderLeftColor = color;
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), 6000);
}

// ---------- bildirimler ----------
function notificationsOn() {
  try { return localStorage.getItem("notify") === "1" && Notification.permission === "granted"; } catch { return false; }
}
function updateNotifyBtn() { $("notifyBtn").textContent = notificationsOn() ? "🔔 Bildirimler açık" : "🔕 Bildirimleri aç"; }
$("notifyBtn").addEventListener("click", async () => {
  if (notificationsOn()) { try { localStorage.setItem("notify", "0"); } catch {} updateNotifyBtn(); return; }
  const p = await Notification.requestPermission();
  try { localStorage.setItem("notify", p === "granted" ? "1" : "0"); } catch {}
  updateNotifyBtn();
  if (p === "granted") new Notification("JARVIS", { body: "Bildirimler açık. Kaplumbağa ve Sörfçü'nün her hamlesini haber vereceğim.", icon: "/icon.svg" });
});
function notify(ev) {
  const b = BOTS[ev.bot] || { label: "JARVIS", icon: "/icon.svg", color: "" };
  toast(`${b.label} · ${ev.kind}: ${ev.message}`, ev.kind === "HATA" || ev.kind === "DUR" ? "#ef4444" : b.color);
  if (notificationsOn()) new Notification(`${b.label} · ${ev.kind}`, { body: ev.message, icon: b.icon });
}

// ---------- düğmeler ----------
$("killBtn").addEventListener("click", () => {
  const on = state.overview?.kill_switch;
  const q = on ? "Acil durdurma kaldırılsın mı? Botlar risk sınırları içinde yeniden işlem açabilir."
               : "ACİL DURDUR: İki bot da yeni işlem açmayacak. Emin misin?";
  if (confirm(q)) control(on ? "kill_off" : "kill_on");
});
for (const name of Object.keys(BOTS)) {
  const btn = document.querySelector(`#bot-${name} [data-f=btn]`);
  btn.addEventListener("click", () => {
    const running = state.overview?.bots?.[name]?.running;
    if (running && !confirm(`${BOTS[name].label} durdurulsun mu? Pozisyonları kayıtlı kalır, açılınca devam eder.`)) return;
    control(running ? "bot_stop" : "bot_start", name);
  });
}

// ---------- genel durum ----------
async function refreshOverview() {
  let o;
  try { o = await api("/api/overview"); } catch { toast("Panel sunucusuna ulaşılamıyor", "#ef4444"); return; }
  state.overview = o;
  $("ruleBadge").textContent = `Ekip kuralı: ${RULE_NAMES[o.rule] || o.rule}`;
  const kill = $("killBtn");
  kill.textContent = o.kill_switch ? "Acil durdurmayı kaldır" : "ACİL DURDUR";
  kill.classList.toggle("active", !!o.kill_switch);

  const bots = Object.entries(o.bots || {});
  const halted = bots.filter(([, b]) => b.detail?.fren?.durdu).map(([, b]) => b.label);
  const dayBlocked = bots.filter(([, b]) => b.detail?.fren?.gun_freni).map(([, b]) => b.label);
  const banner = $("banner");
  if (halted.length) { banner.textContent = `⛔ Düşüş freni çalıştı: ${halted.join(", ")} durdu. Yeniden açmak bilinçli onay ister (jarvis.py fren-sifirla).`; banner.className = "banner bad"; }
  else if (o.kill_switch) { banner.textContent = "⚠ Acil durdurma açık: botlar yeni işlem açmıyor. Açık pozisyonların stopları çalışmaya devam ediyor."; banner.className = "banner"; }
  else if (dayBlocked.length) { banner.textContent = `⚠ Günlük fren: ${dayBlocked.join(", ")} bugün yeni işlem açmayacak (fren dayanışması diğer botu da durdurur).`; banner.className = "banner"; }
  else banner.className = "banner hidden";

  // Ekip toplamı
  const withData = bots.filter(([, b]) => b.equity != null);
  if (withData.length) {
    const eq = withData.reduce((a, [, b]) => a + b.equity, 0);
    const start = withData.reduce((a, [, b]) => a + (b.start_equity || b.equity), 0);
    const dayStart = withData.reduce((a, [, b]) => a + (b.detail?.fren?.gun_baslangic || b.equity), 0);
    const cash = withData.reduce((a, [, b]) => a + b.cash, 0);
    const positions = withData.flatMap(([n, b]) => Object.keys(b.detail?.pozisyonlar || {}).map((s) => `${BOTS[n].label}: ${short(s)}`));
    $("kEquity").textContent = usd(eq);
    $("kEquitySub").textContent = `başlangıç ${usd(start, 0)}`;
    setKpi("kDay", pct((eq / dayStart - 1) * 100), eq - dayStart);
    setKpi("kTotal", pct((eq / start - 1) * 100), eq - start);
    $("kCash").textContent = usd(cash);
    $("kCashSub").textContent = `sermayenin %${num((cash / eq) * 100, 0)}'i nakitte`;
    $("kPos").textContent = String(positions.length);
    $("kPosSub").textContent = positions.length ? positions.join(" · ") : "botlar nakitte bekliyor";
  }
  for (const [name, b] of bots) renderBot(name, b);
  renderRadio(o);
}
function setKpi(id, text, sign) { const e = $(id); e.textContent = text; e.className = "value " + cls(sign); }

function renderBot(name, b) {
  const root = $(`bot-${name}`);
  if (!root) return;
  const f = (k) => root.querySelector(`[data-f=${k}]`);
  const hb = b.heartbeat;
  const alive = b.running && hb && hb.age < 60;
  const pill = f("pill");
  pill.textContent = alive ? `● ${hb.status}` : b.running ? "● Başlıyor…" : "● Kapalı";
  pill.className = "pill " + (alive ? (hb.status === "Çalışıyor" ? "ok" : "warn") : "bad");
  f("btn").textContent = b.running ? "Durdur" : "Başlat";

  const d = b.detail || {}, fren = d.fren || {};
  f("equity").textContent = usd(b.equity);
  const ret = b.start_equity ? (b.equity / b.start_equity - 1) * 100 : null;
  f("ret").textContent = pct(ret); f("ret").className = cls(ret);
  const dd = fren.zirve ? (b.equity / fren.zirve - 1) * 100 : null;
  f("dd").textContent = pct(dd); f("dd").className = dd < 0 ? "down" : "";
  f("risk").textContent = b.risk ? `%${num(b.risk * 100, 0)} / %${num(b.brake * 100, 0)}` : "—";

  const coins = [], thoughts = [];
  for (const [sym, a] of Object.entries(d.analiz || {})) {
    const price = d.fiyat?.[sym], s = short(sym), p = d.pozisyonlar?.[sym];
    const c = el("div", "coin"), row = el("div", "row");
    row.append(el("span", "sym", s), el("span", "px", usd(price)),
               el("span", "pill " + (a.trend_yukari ? "ok" : "bad"), name === "sorfcu" ? (a.trend_yukari ? "🌊 Dalga var" : "Dalga yok") : (a.trend_yukari ? "▲ Trend yukarı" : "▼ Trend aşağı")));
    c.append(row);
    if (name === "kaplumbaga") {
      c.append(el("div", "lv", `Alım: 4s kapanış > ${usd(a.giris_seviyesi, 0)} (${pct((a.giris_seviyesi / price - 1) * 100)}) · Çıkış: < ${usd(a.cikis_seviyesi, 0)} · EMA200: ${usd(a.trend_seviyesi, 0)}`));
    } else {
      c.append(el("div", "lv", `Dalga şartı: günlük kapanış > EMA50 (${usd(a.giris_seviyesi, 0)}) ve EMA50 > EMA200 (${usd(a.trend_seviyesi, 0)})`));
    }
    if (p) {
      const pnlPct = (p.kar_zarar / (p.adet * p.ort_giris)) * 100;
      c.append(el("div", "lv " + cls(p.kar_zarar), `Pozisyon: ${num(p.kar_zarar)} $ (${pct(pnlPct)}) · giriş ${usd(p.ort_giris, 0)} · stop ${usd(p.stop, 0)} (${pct((p.stop / price - 1) * 100)})`));
      thoughts.push(`${s} pozisyonundayım (${pct(pnlPct)}). Stopum ${usd(p.stop, 0)}; fiyat yükseldikçe stopu yukarı taşıyorum.`);
    } else if (name === "kaplumbaga") {
      const dist = (a.giris_seviyesi / price - 1) * 100;
      thoughts.push(!a.trend_yukari ? `${s} düşüş trendinde: kuralım gereği alım yok, kabuğumda bekliyorum.`
        : dist > 0 ? `${s} yukarı trendde. 4 saatlik kapanış ${usd(a.giris_seviyesi, 0)} üstüne çıkarsa giriyorum (%${num(dist, 1)} uzakta).`
        : `${s} kırılım seviyesinde! Bar kapanışında alımı değerlendireceğim.`);
    } else {
      const toFast = (a.giris_seviyesi / price - 1) * 100;
      thoughts.push(a.trend_yukari ? `${s}'de dalga var. Bir sonraki günlük kapanışta tahtaya biniyorum.`
        : price < a.giris_seviyesi ? `${s}'de dalga yok: fiyat EMA50'nin %${num(toFast, 1)} altında. Kıyıda bekliyorum.`
        : `${s} EMA50 üstünde ama büyük trend (EMA50 > EMA200) henüz oluşmadı. Dalganın büyümesini bekliyorum.`);
    }
    coins.push(c);
  }
  f("coins").replaceChildren(...coins);
  if (!b.running) thoughts.unshift("Şu an kapalıyım. “Başlat” ile açabilirsin; pozisyonlarım kayıtlı.");
  f("thought").textContent = thoughts.join(" ") || "Veri bekleniyor…";
}

async function renderRadio(o) {
  let board;
  try { board = await api("/api/board"); } catch { return; }
  const view = {};
  for (const v of board.views) view[`${v.bot}|${v.symbol}`] = v;
  const flags = Object.fromEntries(board.flags.map((f) => [f.bot, f]));
  const grid = $("radio");
  const head = (name) => { const h = el("div", "h"); h.append(img(BOTS[name].icon, "", BOTS[name].label), document.createTextNode(BOTS[name].label)); return h; };
  const cells = [el("div", "h", "Coin"), head("kaplumbaga"), head("sorfcu")];
  const symbols = [...new Set(board.views.map((v) => v.symbol))].sort();
  const msgs = [];
  for (const s of symbols) {
    const t = view[`kaplumbaga|${s}`], u = view[`sorfcu|${s}`];
    const txt = (v, kind) => !v ? "haber yok" : `${v.trend_up ? (kind === "s" ? "🌊 dalga var" : "▲ yukarı") : (kind === "s" ? "dalga yok" : "▼ aşağı")}${v.in_pos ? " · pozisyonda" : ""}`;
    cells.push(el("div", "sym", short(s)), el("div", "cell", txt(t, "k")), el("div", "cell", txt(u, "s")));
    if (t && u) {
      if (t.trend_up && u.trend_up) msgs.push(["good", `${short(s)}: İki bot da yukarı görüyor. Kısa ve uzun vade aynı yönde: güçlü trend.`]);
      else if (t.trend_up && !u.trend_up) msgs.push(["", `${short(s)}: 🐢 → 🏄 “Kısa vadede yükseliş başladı, büyük dalga gelebilir, hazır ol.”`]);
      else if (!t.trend_up && u.trend_up) msgs.push(["warn", `${short(s)}: 🐢 → 🏄 “4 saatlikte zayıflama var, dalgan bitmek üzere olabilir; stopun yerinde.”`]);
      else msgs.push(["", `${short(s)}: İki bot da düşüş görüyor. Ekip nakitte bekliyor; sermaye korunuyor.`]);
    }
  }
  grid.replaceChildren(...cells);
  const rule = o.rule;
  const fk = flags.kaplumbaga, fs = flags.sorfcu;
  const braked = [fk, fs].some((f) => f && (f.halted || f.day_blocked));
  if (rule === "fren_dayanismasi" || rule === "hepsi")
    msgs.unshift([braked ? "bad" : "good", braked ? "🛡 Fren dayanışması devrede: bir botun freni çalıştı, diğeri de yeni işlem açmıyor."
                                        : "🛡 Fren dayanışması hazır: iki botun freni de normal. Biri frene basarsa diğeri de yeni işlem açmaz."]);
  if (!symbols.length) msgs.push(["", "Botlar henüz haber paylaşmadı. Çalışmaya başlayınca burada konuşacaklar."]);
  const acting = (ALARMS_ACTING[o.alarms] || []).map((k) => ALARM_NAMES[k]);
  msgs.push(["", `📨 Haberleşme: her bot kendi kulvarında karar verir. Önemli olayda (trend kırıldı, sert düşüş, kırılım) diğerine haber gider; alan kendi kuralıyla hemen değerlendirir. İşlemle tepki: ${acting.join(", ") || "yok"}.`]);
  $("radioMsgs").replaceChildren(...msgs.map(([k, t]) => el("li", k, t)));
  renderAlarms();
}

async function renderAlarms() {
  let rows;
  try { rows = await api("/api/messages"); } catch { return; }
  const list = $("alarmLog");
  if (!rows.length) { list.replaceChildren(el("li", "muted", "Henüz önemli bir olay olmadı. Botlar sessizce kendi işini yapıyor.")); return; }
  list.replaceChildren(...rows.map((m) => {
    const li = el("li"), route = el("div", "route"), s = BOTS[m.sender], r = BOTS[m.receiver];
    route.append(img(s.icon, "", s.label), document.createTextNode("→"), img(r.icon, "", r.label),
                 el("span", "k", `${short(m.symbol)} ${ALARM_NAMES[m.kind] || m.kind}`), el("span", "tm", time(m.ts)));
    const acted = (m.response || "").startsWith("Uyguladım");
    li.append(route, el("div", "resp" + (acted ? " acted" : ""),
      m.response ? `${r.label}: ${m.response}` : `${r.label} değerlendiriyor…`));
    return li;
  }));
}

// ---------- olaylar ----------
async function refreshEvents() {
  let evs;
  try { evs = await api(`/api/events?after=${state.lastEventId}`); } catch { return; }
  const list = $("events");
  for (const ev of evs) {
    const b = BOTS[ev.bot];
    const li = el("li");
    li.append(el("span", "muted", time(ev.ts)), b ? img(b.icon, "ev-icon", b.label) : el("span"),
              el("span", `tag ${ev.kind}`, ev.kind), el("span", null, ev.message));
    list.prepend(li);
    if (!state.firstEvents && NOTIFY_KINDS.has(ev.kind)) notify(ev);
    state.lastEventId = Math.max(state.lastEventId, ev.id);
  }
  while (list.children.length > 300) list.lastChild.remove();
  state.firstEvents = false;
}

async function refreshTrades() {
  let rows;
  try { rows = await api("/api/trades"); } catch { return; }
  const body = $("trades");
  if (!rows.length) { const tr = el("tr"); const td = el("td", "muted", "Henüz işlem yok. Botlar seçici: doğru fırsatı bekliyorlar."); td.colSpan = 6; tr.append(td); body.replaceChildren(tr); return; }
  body.replaceChildren(...rows.map((t) => {
    const tr = el("tr"), b = BOTS[t.bot], who = el("td");
    if (b) who.append(img(b.icon, "tr-icon", b.label), document.createTextNode(" " + b.label));
    tr.append(who, el("td", null, short(t.symbol)), el("td", null, `${usd(t.entry, 0)} · ${time(t.entry_time)}`),
              el("td", null, `${usd(t.exit, 0)} · ${time(t.exit_time)}`), el("td", cls(t.pnl), `${num(t.pnl)} $`), el("td", "muted", t.reason));
    return tr;
  }));
}

async function refreshBacktests() {
  let rows;
  try { rows = await api("/api/backtests"); } catch { return; }
  const period = (p) => p.startsWith("2024") ? "2024+ (görülmemiş)" : p.endsWith("2023-12-31") ? "2020–2023" : "Tümü";
  const ALARM_SET_NAMES = { yok: "haber yok", trend: "trend haberi", trend_dusus: "trend + sert düşüş", tam: "tüm haberler" };
  const label = (n) => n.startsWith("EKIP_") ? `🐢+🏄 Ekip · ${RULE_NAMES[n.slice(5)] || n.slice(5)}`
    : n.startsWith("HABER_") ? `📨 Haberleşme · ${ALARM_SET_NAMES[n.slice(6)] || n.slice(6)}` : n;
  const live = `HABER_${state.overview?.alarms}`;
  rows.sort((a, b) => (b.name === live) - (a.name === live) || a.name.localeCompare(b.name));
  $("backtests").replaceChildren(...rows.map((r) => {
    const s = r.summary, tr = el("tr", r.name === live ? "best" : "");
    tr.append(el("td", null, label(r.name) + (r.name === live ? " ✓ canlıda" : "")), el("td", "muted", period(r.period)),
              el("td", cls(s["toplam_getiri_%"]), pct(s["toplam_getiri_%"])), el("td", null, pct(s["yillik_getiri_%"])),
              el("td", "down", pct(s["en_buyuk_dusus_%"])), el("td", null, num(s.sharpe)), el("td", null, s.islem_sayisi));
    return tr;
  }));
}

// ---------- kontrol raporu ----------
$("reportBtn").addEventListener("click", () => {
  control("report_now");
  setTimeout(refreshReport, 45000); setTimeout(refreshReport, 90000);
});
async function refreshReport() {
  let r;
  try { r = await api("/api/report"); } catch { return; }
  if (!r) return;
  const kind = r.karar === "UYUMLU" ? "ok" : r.karar === "UYARI" ? "warn" : "bad";
  const text = { ok: "Canlı = test ✓", warn: "Dikkat edilmeli", bad: "İncelenmeli" }[kind];
  $("reportDate").textContent = `son kontrol: ${time(r.tarih)}`;
  const v = el("div", `verdict ${kind}`, r.karar);
  v.append(el("small", null, text));
  const cards = Object.entries(r.botlar || {}).map(([name, b]) => {
    const c = el("div", "rep-bot"), h = el("div", "h");
    if (BOTS[name]) h.append(img(BOTS[name].icon, "", b.ad));
    h.append(document.createTextNode(b.ad),
             el("span", "pill " + (b.karar === "UYUMLU" ? "ok" : b.karar === "UYARI" ? "warn" : "bad"), b.karar));
    c.append(h,
      el("div", null, `İşlemler: canlı ${b.canli_islem} · test ${b.test_islem} · eşleşen ${b.eslesen}`),
      el("div", null, `Açık kalma: %${num(b["calisma_%"], 1)} (${num(b.gun, 1)} gün) · hata (7 gün): ${b.hata_7gun}`),
      el("div", null, `Açık pozisyon: canlı ${b.canli_acik.length ? b.canli_acik.map(short).join(", ") : "yok"} · test ${b.test_acik.length ? b.test_acik.map(short).join(", ") : "yok"}`));
    const notes = [...b.farklar, ...b.kapali_araliklar.map((x) => `Kapalıydı: ${x}`), ...b.frenler];
    if (notes.length) { const ul = el("ul"); notes.slice(0, 6).forEach((n) => ul.append(el("li", null, n))); c.append(ul); }
    return c;
  });
  $("report").replaceChildren(v, ...cards);
}

// ---------- grafik ----------
async function refreshEquity() {
  try { state.equity = await api("/api/equity"); } catch { return; }
  drawChart();
}
function drawChart() {
  const c = $("chart"), ctx = c.getContext("2d"), dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = 260;
  c.width = w * dpr; c.height = h * dpr; ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  const pts = state.equity;
  ctx.fillStyle = "#8a96ad"; ctx.font = "12px Segoe UI";
  if (pts.length < 2) { ctx.fillText("Veri toplanıyor… botlar her dakika bir nokta ekler.", 16, h / 2); return; }
  // Botlar 500 $, ekip 1000 $ ile başlar: hepsini % getiri olarak çiz
  const series = [["ekip", "#e6ebf5", 2.4], ["kaplumbaga", BOTS.kaplumbaga.color, 1.6], ["sorfcu", BOTS.sorfcu.color, 1.6]]
    .map(([k, color, lw]) => ({ color, lw, vals: pts.map((p) => (p[k] / pts[0][k] - 1) * 100) }));
  const all = series.flatMap((s) => s.vals);
  let lo = Math.min(...all, 0), hi = Math.max(...all, 0);
  const pad = (hi - lo) * 0.1 || 0.05; lo -= pad; hi += pad;
  const L = 56, R = 10, T = 10, B = 24;
  const x = (i) => L + (i / (pts.length - 1)) * (w - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (h - T - B);
  ctx.strokeStyle = "#243049"; ctx.lineWidth = 1;
  for (let k = 0; k <= 4; k++) {
    const v = lo + (k / 4) * (hi - lo), yy = y(v);
    ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(w - R, yy); ctx.stroke();
    ctx.fillText(`${num(v, 1)}%`, 4, yy + 4);
  }
  ctx.setLineDash([4, 4]); ctx.strokeStyle = "#8a96ad";
  ctx.beginPath(); ctx.moveTo(L, y(0)); ctx.lineTo(w - R, y(0)); ctx.stroke(); ctx.setLineDash([]);
  for (const s of series.slice().reverse()) {
    ctx.beginPath(); s.vals.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
    ctx.strokeStyle = s.color; ctx.lineWidth = s.lw; ctx.stroke();
  }
  ctx.fillStyle = "#8a96ad";
  ctx.fillText(time(pts[0].ts), L, h - 6);
  const endLabel = time(pts[pts.length - 1].ts);
  ctx.fillText(endLabel, w - R - ctx.measureText(endLabel).width, h - 6);
}
window.addEventListener("resize", drawChart);

// ---------- döngüler ----------
function tick() { $("clock").textContent = new Date().toLocaleTimeString("tr-TR"); }
updateNotifyBtn();
tick(); setInterval(tick, 1000);
refreshOverview().then(() => { refreshEquity(); refreshBacktests(); });
setInterval(refreshOverview, 5000);
refreshEvents(); setInterval(refreshEvents, 5000);
refreshTrades(); setInterval(refreshTrades, 30000);
setInterval(refreshEquity, 60000);
setInterval(refreshBacktests, 300000);
refreshReport(); setInterval(refreshReport, 300000);
