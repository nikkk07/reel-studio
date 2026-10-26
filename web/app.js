// Reel Studio – static app. Talks only to Supabase; the Mac worker picks jobs up from the queue.
import { SUPABASE_URL, SUPABASE_ANON_KEY } from "./config.js";

// stay signed in across refreshes and restarts: session kept in this browser's storage and renewed automatically
const sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false } });   // supabase-js 2.45.4, pinned with SRI
const $app = document.getElementById("app");
const S = { session: null, route: "create", worker: null, reels: [], est: null, seenReview: null, mode: "suggest", picked: null,
  concept: null, open: {}, sel: {}, timers: [], installEvt: null, checkedAt: 0,
  confirm: {}, busy: {}, drafts: {}, changeOpen: {}, net: { ok: true, since: 0 }, pending: {}, waiters: {}, tick: {}, idea: "", steer: "" };
try { Object.assign(S.pending, JSON.parse(sessionStorage.getItem("rs_pending") || "{}")); } catch { /* */ }
const savePending = () => { try { sessionStorage.setItem("rs_pending", JSON.stringify(S.pending)); } catch { /* */ } };

// ---------------------------------------------------------------- constants
const STAGES = ["script", "voice", "footage", "render", "qc", "publish"];
const STAGE_NAME = { script: "Script", voice: "Voice", footage: "Footage", render: "Render", qc: "Check", publish: "Publish" };
const STAGE_HELP = { script: "Claude researches sources and writes the narration", voice: "The cloned voice records each part, best take wins",
  footage: "Stock clips are found and Claude picks the right shots", render: "Frames, graphics, sound mix and mastering",
  qc: "Claude watches the finished reel and checks the numbers", publish: "Upload for review and save the master on the Mac" };
const DEFAULT_SEC = { script: 300, voice: 1500, footage: 300, render: 720, qc: 300, publish: 60 };
const DEFAULT_NODES = {
  script: [["research", "Research sources"], ["write", "Write narration"], ["facts", "Fact list"]],
  voice: [["parts", "Record each part"], ["score", "Pick clearest takes"]],
  footage: [["search", "Find clips"], ["pick", "Claude chooses clips"]],
  render: [["frames", "Draw frames"], ["mix", "Mix sound"], ["master", "Master & encode"], ["sheet", "Contact sheet"]],
  qc: [["auto", "Loudness, size, length"], ["watch", "Claude watches the reel"]],
  publish: [["video", "Upload reel"], ["archive", "Save master on Mac"]],
};
const ACTIVE = ["queued", "running", "waiting_quota"];

// ---------------------------------------------------------------- DOM helpers (text nodes only, no HTML strings from data)
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "cls") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}
const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  for (const c of kids.flat(Infinity)) if (c != null) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}
const ICON = {
  home: "M4 11.5 12 4l8 7.5M6.5 9.5V20h11V9.5M10 20v-5h4v5", ext: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  plane: "M21 11.2 13.6 10 9.8 3H8.2l1.6 7.1-4.7.4-1.7-2.3H2.2l1 3.8-1 3.8h1.2l1.7-2.3 4.7.4L8.2 21h1.6l3.8-7 7.4-1.2c.6-.1 1-.4 1-.8s-.4-.7-1-.8Z",
  create: "M12 4v16M4 12h16", route: "M5 18c3-9 11-3 14-12M5 18a2 2 0 1 0 0 .1M19 6a2 2 0 1 0 0 .1",
  library: "M4 4h6v7H4zM14 4h6v4h-6zM14 12h6v8h-6zM4 15h6v5H4z", agents: "M12 3v3M8.5 8h7a3 3 0 0 1 3 3v5a3 3 0 0 1-3 3h-7a3 3 0 0 1-3-3v-5a3 3 0 0 1 3-3zM9.5 13h.01M14.5 13h.01M3 13h2.5M18.5 13H21", check: "M5 12.5l4.5 4.5L19 7.5", x: "M6 6l12 12M18 6 6 18",
  play: "M8 5v14l11-7z", down: "M12 4v12m0 0-5-5m5 5 5-5M5 20h14", bell: "M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15zM10 20a2 2 0 0 0 4 0",
};
function icon(name, size = 18, fill = false) {
  return s("svg", { viewBox: "0 0 24 24", width: size, height: size, "aria-hidden": "true", fill: fill ? "currentColor" : "none",
    stroke: fill ? "none" : "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }, s("path", { d: ICON[name] }));
}
const safeUrl = (u) => (/^https?:\/\//i.test(String(u || "")) ? u : null);
const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return "source"; } };
const srcChip = (url, title) => safeUrl(url) ? h("a", { cls: "src", href: url, target: "_blank", rel: "noopener noreferrer", title: title || url }, title ? `${host(url)}` : host(url)) : null;
const clock = (d) => new Date(d).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const dateShort = (d) => new Date(d).toLocaleDateString([], { day: "numeric", month: "short" });
const mins = (sec) => { sec = Math.max(0, sec); if (sec < 90) return "about 1 min"; const m = Math.round(sec / 60); return m < 90 ? `about ${m} min` : `about ${Math.floor(m / 60)} h ${m % 60} min`; };
const shortMins = (sec) => `${Math.max(1, Math.round(sec / 60))} min`;
const ago = (iso) => { const s = (Date.now() - new Date(iso)) / 1000; return s < 60 ? "just now" : s < 3600 ? `${s / 60 | 0} min ago` : s < 86400 ? `${s / 3600 | 0} h ago` : `${s / 86400 | 0} d ago`; };
const callsign = (id) => `RS ${id.slice(0, 4).toUpperCase()}`;
const errText = (e) => { const m = (e && (e.message || e.error_description)) || String(e); return /row-level security|not a member/i.test(m) ? "Your account isn't on the members list yet." : m; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const titleOf = (j) => j.output?.title || j.input?.concept?.title || "Untitled reel";
const capParts = (text) => String(text || "").split(/(\*[^*]+\*)/).filter(Boolean).map((p) => p.startsWith("*") ? h("span", { cls: "hl" }, p.slice(1, -1)) : p);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

function toast(msg, opts = {}) {
  let box = document.querySelector(".toasts"); if (!box) { box = h("div", { cls: "toasts", role: "status", "aria-live": "polite" }); document.body.append(box); }
  const t = h("div", { cls: "toast" + (opts.err ? " err" : "") }, h("span", {}, msg), opts.action ? h("button", { onclick: () => { opts.action.fn(); t.remove(); } }, opts.action.label) : null);
  box.append(t); setTimeout(() => t.remove(), opts.ms || 5000);
}
function every(ms, fn) { fn(); S.timers.push(setInterval(fn, ms)); }
function clearTimers() { S.timers.forEach(clearInterval); S.timers = []; }

// ---------------------------------------------------------------- data
async function rpc(fn, args) { const { data, error } = await sb.rpc(fn, args); if (error) throw error; return data; }
const createJob = (kind, input, parent = null) => rpc("create_job", { p_kind: kind, p_input: { ...input, brand: S.brand }, p_parent: parent });
// ---------------------------------------------------------------- accounts (brands): same studio, each account keeps its own reels
const BRANDS_FALLBACK = [{ id: "weone", name: "We One Aviation", tagline: "DGCA ground classes, Dwarka · Delhi", website: "https://weoneaviation.in", logo: "brands/weone.png", status: "live", instagram: "we_one_aviation" }];
try { S.brand = localStorage.getItem("rs_brand") || "weone"; } catch { S.brand = "weone"; }
const brandOf = (id = S.brand) => (S.brands || BRANDS_FALLBACK).find((b) => b.id === id) || BRANDS_FALLBACK[0];
const mine = (j) => (j.brand || "weone") === S.brand;
const isLive = (id = S.brand) => brandOf(id).status === "live";
function setBrand(id) { S.brand = id; S.lastSuggest = null; S.picked = null; S.concept = null; try { localStorage.setItem("rs_brand", id); } catch { /* */ } }
async function loadBrands() {
  const { data, error } = await sb.from("brands").select("id,name,tagline,website,logo,color,instagram,status,sort,logo_asset,logo_updated_at,intel_at").order("sort");
  if (!error && data?.length) { S.brands = data; if (!data.some((b) => b.id === S.brand)) setBrand("weone"); }
  return S.brands;
}
// account logos: the one uploaded in Assets (private storage, signed link), else the built-in file
S.logoUrls = {};
function logoSrc(b) { const c = S.logoUrls[b.id]; return (b.logo_asset && c?.path === b.logo_asset && c.url) || b.logo || "icons/icon-192.png"; }
async function ensureLogo(b) {
  if (!b?.logo_asset) return; const c = S.logoUrls[b.id];
  if (c?.path === b.logo_asset && (c.url || c.busy) && Date.now() - c.at < 50 * 60e3) return;
  S.logoUrls[b.id] = { path: b.logo_asset, busy: true, at: Date.now() };
  const { data } = await sb.storage.from("brand-assets").createSignedUrl(b.logo_asset, 3600).catch(() => ({}));
  S.logoUrls[b.id] = { path: b.logo_asset, url: data?.signedUrl || "", at: Date.now() };
  if (data?.signedUrl) document.querySelectorAll(`img[data-logo="${b.id}"]`).forEach((im) => { im.src = data.signedUrl; });
}
const brandLogo = (b, size = 40) => { ensureLogo(b); return h("span", { cls: "blogo", style: `width:${size}px;height:${size}px` }, h("img", { src: logoSrc(b), alt: "", loading: "lazy", "data-logo": b.id })); };
const PLAT_IMG = { ig: "icons/platforms/instagram.png", fb: "icons/platforms/facebook.png", li: "icons/platforms/linkedin.png", web: "icons/platforms/website.png" };
async function getJob(id) { const { data, error } = await sb.from("jobs").select("*").eq("id", id).single(); if (error) throw error; return data; }
async function events(id, n = 10) { const { data } = await sb.from("job_events").select("id,ts,level,message").eq("job_id", id).order("id", { ascending: false }).limit(n); return data || []; }
async function loadWorker() { const { data } = await sb.from("workers").select("*").order("last_seen", { ascending: false }).limit(1); S.worker = (data || [])[0] || null; return S.worker; }
const COLS = "id,brand,kind,status,stage,progress,input,output,error,not_before,created_at,updated_at,attempts,parent_id,lease_owner,lease_expires_at";
const normTitle = (j) => String(j.input?.concept?.title || j.output?.title || "").trim().toLowerCase();
async function loadReels() {
  // live reels and recent stopped ones are fetched separately, so old history can never push a running reel off the list;
  // on any error the last good list is kept (a dropped connection must not make reels disappear)
  const since = new Date(Date.now() - 12 * 3600e3).toISOString();     // stopped / failed reels leave the list after 12 hours
  const [live, stopped] = await Promise.all([
    sb.from("jobs").select(COLS).eq("kind", "reel").in("status", [...ACTIVE, "review"]).order("created_at", { ascending: true }).limit(50),
    sb.from("jobs").select(COLS).eq("kind", "reel").in("status", ["failed", "cancelled"]).gte("updated_at", since).order("updated_at", { ascending: false }).limit(12)]);
  const err = live.error || stopped.error; if (err) throw err;
  const busyTitles = new Set(live.data.map(normTitle).filter(Boolean));
  S.reels = [...live.data, ...stopped.data.filter((j) => !["reject", "auto_revise"].includes(j.output?.decision) && !busyTitles.has(normTitle(j)))];
  return S.reels;
}
const stopping = (j) => j.status === "cancelled" && j.lease_owner && new Date(j.lease_expires_at) > Date.now();
function patchLocal(row) { if (!row || !row.id) return; const i = S.reels.findIndex((x) => x.id === row.id); if (i >= 0) S.reels[i] = { ...S.reels[i], ...row }; else if (row.kind === "reel") S.reels.push(row); }
const isAuthErr = (e) => e && (e.code === "PGRST301" || e.code === "PGRST303" || /jwt expired|invalid jwt|jwt.*(malformed|signature)/i.test(e.message || ""));
async function signed(path, download) {
  if (!path) return null;
  const { data } = await sb.storage.from("reels").createSignedUrl(path, 3600, download ? { download } : undefined); return data?.signedUrl || null;
}
async function download(path, name) {     // signs a fresh link at tap time (links in an old page expire after an hour)
  const u = await signed(path, name); if (!u) { toast("Download link could not be created. Try again.", { err: true }); return; }
  const a = h("a", { href: u, download: name, style: "display:none" }); document.body.append(a); a.click(); a.remove();
}
const macAlive = () => S.worker && Date.now() - new Date(S.worker.last_seen) < 75e3;

// ---------------------------------------------------------------- time estimates (learned from finished reels)
async function loadEstimates() {
  const { data } = await sb.from("jobs").select("output").eq("kind", "reel").in("status", ["review", "done"]).order("updated_at", { ascending: false }).limit(8);
  const rows = (data || []).map((r) => r.output?.timings).filter((t) => t && Object.keys(t).length >= 5);
  const sec = {};
  for (const st of STAGES) {
    const v = rows.map((t) => t[st]).filter((x) => x > 0).sort((a, b) => a - b);
    sec[st] = v.length ? v[Math.floor(v.length / 2)] : DEFAULT_SEC[st];
  }
  S.est = { sec, basis: rows.length };
}
const totalSec = () => STAGES.reduce((a, st) => a + S.est.sec[st], 0);
function remainingSec(j) {
  if (!ACTIVE.includes(j.status)) return 0;
  const t = j.output?.timings || {}; const started = j.output?.stage_started || {};
  let rem = 0;
  for (const st of STAGES) {
    if (t[st] != null) continue;
    if (j.status === "running" && st === j.stage && started[st]) {
      const el = Date.now() / 1000 - started[st]; rem += Math.max(S.est.sec[st] - el, S.est.sec[st] * 0.08);
    } else rem += S.est.sec[st];
  }
  if (j.status === "waiting_quota" && j.not_before) rem += Math.max(0, (new Date(j.not_before) - Date.now()) / 1000);
  return rem;
}
function etaFor(j) {   // reels run one at a time on the Mac, in order
  let t = 0;
  for (const r of S.reels.filter((x) => ACTIVE.includes(x.status))) { t += remainingSec(r); if (r.id === j.id) break; }
  return { left: t, at: Date.now() + t * 1000 };
}
function etaNew() {
  const ahead = S.reels.filter((x) => ACTIVE.includes(x.status));
  const wait = ahead.reduce((a, r) => a + remainingSec(r), 0);
  const left = wait + totalSec();
  return { left, at: Date.now() + left * 1000, ahead: ahead.length };
}

// ---------------------------------------------------------------- alerts: notification, chime, vibration, tab title
function chime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)(); const now = ctx.currentTime;
    [[659.25, 0], [880, 0.16], [1174.66, 0.32]].forEach(([f, d]) => {
      const o = ctx.createOscillator(), g = ctx.createGain(); o.type = "sine"; o.frequency.value = f;
      g.gain.setValueAtTime(0, now + d); g.gain.linearRampToValueAtTime(0.18, now + d + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, now + d + 0.7);
      o.connect(g).connect(ctx.destination); o.start(now + d); o.stop(now + d + 0.75);
    });
  } catch { /* audio blocked */ }
}
async function notifyReady(j) {
  chime(); try { navigator.vibrate?.([90, 60, 90]); } catch { /* */ }
  const auto = j.output?.autopilot_hold ? " Autopilot held it back: it posts only when you approve." : autoOn(j) ? ` It posts to Instagram by itself in ${S.settings?.post_schedule?.review_minutes ?? 5} min unless you act.` : "";
  toast(`Ready for review: ${titleOf(j)}.${auto}`, { ms: 12000, action: { label: "Review", fn: () => go("production", j.id) } });
  if ("Notification" in window && Notification.permission === "granted") {
    const opts = { body: `${titleOf(j)} is ready.${auto}`, icon: "icons/icon-192.png", badge: "icons/icon-192.png", tag: j.id, data: { url: "/#/production" } };
    try { const reg = await navigator.serviceWorker?.getRegistration(); reg ? reg.showNotification("Reel ready", opts) : new Notification("Reel ready", opts); } catch { /* */ }
  }
}
async function askAlerts() {
  if (!("Notification" in window) || Notification.permission !== "default") return;
  try { await Notification.requestPermission(); } catch { /* */ }
}
function updateTitle() {
  const n = S.reels.filter((j) => j.status === "review").length;
  document.title = n ? `(${n}) Reel ready – Reel Studio` : "Reel Studio";
}

// ---------------------------------------------------------------- shell
function shell(content, { nav = true } = {}) {
  const reviewN = S.reels.filter((j) => j.status === "review" && mine(j)).length;
  const activeN = S.reels.filter((j) => ACTIVE.includes(j.status) && mine(j)).length;
  const badgeN = reviewN || activeN;
  const items = [["home", "Dashboard", "home"], ["create", "Create", "create"], ["production", "Production", "route"], ["library", "Library", "library"], ["agents", "Agents", "agents"]];
  const navBtn = ([k, label, ic], mobile) => h("button", { "aria-current": S.route === k ? "page" : null, onclick: () => go(k) },
    mobile ? icon(ic, 22) : null, label, k === "production" && badgeN ? h("span", { cls: "badge", "aria-label": `${badgeN} reels` }, badgeN) : null);
  const lamps = S.session ? h("button", { cls: "status", onclick: () => go("check"), title: "Run the pre-flight check" },
    h("span", {}, h("i", { cls: "lamp " + (macAlive() ? "ok" : "bad") }), h("span", { cls: "label" }, "Mac")),
    h("span", {}, h("i", { cls: "lamp " + (!macAlive() ? "" : S.worker?.claude_ok ? "ok" : "bad") }), h("span", { cls: "label" }, "Claude"))) : null;
  const initials = (S.session?.user?.email || "?").slice(0, 2).toUpperCase();
  const top = h("header", { cls: "top" }, h("div", { cls: "top-in" },
    S.route === "home" || !S.session
      ? h("a", { cls: "brand plain", href: "#/home", "aria-label": "Reel Studio dashboard" }, h("div", {}, h("b", {}, "Reel Studio"), h("span", {}, "All accounts")))
      : h("button", { cls: "brand acct", onclick: () => go("home"), title: "Switch account", "aria-label": `${brandOf().name}. Switch account` }, brandLogo(brandOf(), 36),
          h("div", {}, h("b", {}, brandOf().name), h("span", {}, "Switch account ▾"))),
    nav && S.session ? h("nav", { cls: "nav", "aria-label": "Main" }, items.map((it) => navBtn(it, false))) : null,
    h("div", { cls: "spacer" }), lamps,
    S.session ? h("button", { cls: "who", "aria-label": "Account menu", onclick: toggleMenu }, initials) : null));
  const tab = nav && S.session ? h("nav", { cls: "tabbar", "aria-label": "Main" }, items.map((it) => navBtn(it, true))) : null;
  $app.replaceChildren(top, h("main", { id: "main" }, content), tab || "");
  updateTitle();
}
function toggleMenu(e) {
  e.stopPropagation(); const old = document.querySelector(".menu"); if (old) { old.remove(); return; }
  const m = h("div", { cls: "menu", role: "menu" },
    h("div", { cls: "who-line" }, S.session.user.email),
    h("button", { cls: "btn quiet", onclick: () => go("check") }, "Run pre-flight check"),
    "Notification" in window && Notification.permission === "default" ? h("button", { cls: "btn quiet", onclick: async () => { await askAlerts(); m.remove(); } }, icon("bell"), "Turn on ready alerts") : null,
    S.installEvt ? h("button", { cls: "btn quiet", onclick: async () => { S.installEvt.prompt(); S.installEvt = null; m.remove(); } }, "Install as app") : null,
    h("button", { cls: "btn quiet danger", onclick: () => sb.auth.signOut() }, "Sign out"));
  document.querySelector(".top").append(m);
  setTimeout(() => document.addEventListener("click", function close(ev) { if (!m.contains(ev.target)) { m.remove(); document.removeEventListener("click", close); } }), 0);
}

// ---------------------------------------------------------------- router
function go(route, focusId) {
  if (focusId) { S.open[focusId] = true; }
  if (location.hash !== `#/${route}`) location.hash = `#/${route}`; else render();
}
window.addEventListener("hashchange", () => render());
function render() {
  clearTimers(); document.querySelector(".menu")?.remove();
  if (!S.session) return viewLogin();
  const r = (location.hash.replace(/^#\/?/, "") || "home").split("/")[0];
  // the pre-flight page shows once per session; later it only returns if the Mac or Claude actually reports a problem
  const fresh = Date.now() - S.checkedAt < 30 * 60e3 || (S.checkedAt > 0 && macAlive() && S.worker?.claude_ok !== false);
  if (r === "check" || !fresh) return viewPreflight(r === "check" ? "home" : (r || "home"));
  S.route = ["home", "create", "production", "library", "agents"].includes(r) ? r : "home";
  ({ home: viewDashboard, create: viewCreate, production: viewProduction, library: viewLibrary, agents: viewAgents })[S.route]();
}

// ---------------------------------------------------------------- login
function routeArt(cls = "chart") {
  const pts = [[30, 150], [130, 70], [230, 120], [330, 50], [430, 110], [530, 40]];
  const d = "M" + pts.map((p) => p.join(" ")).join(" L");
  return s("svg", { class: cls, viewBox: "0 0 560 190", role: "img", "aria-label": "Route from script to publish" },
    s("path", { class: "ghost", d }), s("path", { class: "leg draw", d }),
    pts.map(([x, y], i) => [s("circle", { class: "wp", cx: x, cy: y, r: 8 }), s("text", { x, y: y + 30, "text-anchor": "middle" }, STAGE_NAME[STAGES[i]])]));
}
function viewLogin() {
  const email = h("input", { type: "email", id: "em", autocomplete: "username", required: true, placeholder: "name@company.com" });
  const pass = h("input", { type: "password", id: "pw", autocomplete: "current-password", required: true });
  const msg = h("p", { cls: "small", role: "alert", style: "min-height:22px;margin-top:12px;color:var(--danger)" });
  const btn = h("button", { cls: "btn primary block", type: "submit" }, "Sign in");
  const form = h("form", { cls: "panel signin", onsubmit: async (e) => {
    e.preventDefault(); btn.disabled = true; btn.textContent = "Signing in…"; msg.textContent = "";
    const { error } = await sb.auth.signInWithPassword({ email: email.value.trim(), password: pass.value });
    if (error) { msg.textContent = error.message === "Invalid login credentials" ? "That email and password don't match. Try again." : error.message; btn.disabled = false; btn.textContent = "Sign in"; }
  } }, h("h2", {}, "Sign in"), h("p", { cls: "muted small" }, "Use the account the studio owner created for you."),
    h("label", { cls: "field", for: "em" }, "Email"), email, h("label", { cls: "field", for: "pw" }, "Password"), pass,
    h("div", { style: "height:20px" }), btn, msg);
  shell(h("section", { cls: "login" },
    h("div", { cls: "login-art" }, h("h1", {}, "Scroll-stopping reels. Fact-checked. Ready to post."),
      h("p", {}, "Pick a topic or bring your own. The studio researches real sources, writes the script, records the voice and edits the reel for every brand you run, then waits for your yes."),
      routeArt()), form), { nav: false });
  email.focus();
}

// ---------------------------------------------------------------- pre-flight check (Connect to Server)
function dial(state) {
  return s("svg", { class: "dial " + state, viewBox: "0 0 40 40", "aria-hidden": "true" }, s("circle", { class: "bg", cx: 20, cy: 20, r: 16 }), s("circle", { class: "fg", cx: 20, cy: 20, r: 16 }),
    state === "ok" ? s("path", { d: "M13 20.5l4.5 4.5L27 15.5", stroke: "var(--done)" }) : state === "bad" ? s("path", { d: "M15 15l10 10M25 15 15 25", stroke: "var(--danger)" }) : null);
}
function viewPreflight(next) {
  const st = { mac: ["idle", "Not checked yet"], claude: ["idle", "Not checked yet"] };
  const rows = h("div", { cls: "checks" }); const btn = h("button", { cls: "btn go block", onclick: run }, "Connect to Server");
  const extra = h("div");
  const paint = () => {
    const tag = (x) => h("span", { cls: "state-tag " + (x === "ok" ? "ok" : x === "bad" ? "bad" : "") }, x === "ok" ? "Ready" : x === "bad" ? "Not ready" : x === "wait" ? "Checking" : "Waiting");
    rows.replaceChildren(
      h("div", { cls: "check" }, dial(st.mac[0]), h("div", {}, h("b", {}, "Studio Mac"), h("p", { cls: "muted" }, st.mac[1])), tag(st.mac[0])),
      h("div", { cls: "check" }, dial(st.claude[0]), h("div", {}, h("b", {}, "Claude"), h("p", { cls: "muted" }, st.claude[1])), tag(st.claude[0])));
  };
  paint();
  shell(h("section", { cls: "panel preflight" }, h("h1", {}, "Pre-flight check"),
    h("p", { cls: "muted", style: "margin-top:8px" }, "Confirms the studio Mac is online and Claude is signed in with usage available, before you start a reel."),
    rows, btn, extra), { nav: false });
  async function run() {
    btn.disabled = true; btn.textContent = "Checking…"; extra.replaceChildren();
    st.mac = ["wait", "Calling the Mac…"]; st.claude = ["idle", "Waits for the Mac"]; paint();
    try {
      const { data: ping, error } = await sb.from("pings").insert({}).select().single(); if (error) throw error;
      let ans = null; const t0 = Date.now();
      while (Date.now() - t0 < 15e3) { await sleep(1500); const { data } = await sb.from("pings").select("answered_at,worker_id,reply").eq("id", ping.id).single(); if (data?.answered_at) { ans = data; break; } }
      const w = await loadWorker();
      if (!ans) {
        st.mac = ["bad", w ? `No answer. Last heard from ${ago(w.last_seen)}. It may be asleep, switched off, offline, or the studio service stopped.` : "No Mac has connected yet. Run the setup on the Mac first."];
        st.claude = ["idle", "Needs the Mac"]; paint(); btn.disabled = false; btn.textContent = "Retry"; return;
      }
      const r = ans.reply || {};
      st.mac = ["ok", `Online. ${r.reels_today ?? 0} of ${r.daily_cap ?? 3} reels made today.`]; st.claude = ["wait", "Checking sign-in and usage…"]; paint();
      let ok = r.claude_ok, note = r.claude_note;
      if (r.claude_checking) {
        const t1 = Date.now();
        while (Date.now() - t1 < 90e3) { await sleep(3000); const w2 = await loadWorker(); if (w2?.claude_checked_at && new Date(w2.claude_checked_at) >= new Date(ping.created_at)) { ok = w2.claude_ok; note = w2.claude_note; break; } }
      }
      st.claude = ok ? ["ok", "Signed in and ready."] : ["bad", note ? `Not ready: ${note}` : "No recent answer. Try again in a minute."]; paint();
      if (ok) { S.checkedAt = Date.now(); try { localStorage.setItem("rs_checked", String(S.checkedAt)); } catch { /* */ }
        btn.textContent = "All systems ready"; await sleep(900); location.hash = `#/${next}`; render(); return; }
      btn.disabled = false; btn.textContent = "Retry";
      extra.replaceChildren(h("button", { cls: "btn quiet block", style: "margin-top:8px", onclick: () => { S.checkedAt = Date.now(); go("home"); } }, "Continue to the Dashboard"));
    } catch (e) { st.mac = ["bad", errText(e)]; paint(); btn.disabled = false; btn.textContent = "Retry"; }
  }
}

// ---------------------------------------------------------------- create
function thinkingBox(lineEl, jobId) {
  const nodes = [[20, 45], [60, 15], [60, 75], [100, 30], [100, 62]];
  const edges = [[0, 1], [0, 2], [1, 3], [2, 4], [1, 4], [2, 3]];
  const g = s("svg", { class: "net", viewBox: "0 0 120 90", "aria-hidden": "true" },
    edges.map(([a, b], i) => s("line", { class: i % 2 ? "" : "flow", x1: nodes[a][0], y1: nodes[a][1], x2: nodes[b][0], y2: nodes[b][1] })),
    nodes.map(([x, y], i) => s("circle", { cx: x, cy: y, r: 7, class: i % 2 ? "on" : "", style: `animation-delay:${i * 0.25}s` })));
  const stop = jobId ? h("button", { cls: "btn quiet danger", style: "margin-top:8px", onclick: (e) => stopRequest(jobId, e.currentTarget) }, "Stop") : null;
  return h("div", { cls: "panel thinking" }, g, h("div", {}, lineEl, stop));
}
// One waiter per job, shared by every view: leaving Create and coming back re-attaches to the same request instead of starting a new one.
function waitJob(id, ticker) {
  S.tick[id] = ticker;
  return S.waiters[id] ||= (async () => {
    let lastId = 0, misses = 0;
    const say = (...kids) => { const t = S.tick[id]; if (t) t.replaceChildren(...kids); };
    try {
      for (;;) {
        let j;
        try { j = await getJob(id); misses = 0; }
        catch (e) {
          if (e?.code === "PGRST116") return { id, status: "cancelled", error: "This request no longer exists." };
          misses++; say(h("span", { cls: "now" }, "Connection lost. Still waiting; it reconnects by itself."), h("span", { cls: "faint small" }, "The Mac keeps working meanwhile."));
          await sleep(Math.min(15000, 2500 * misses)); continue;
        }
        if (!ACTIVE.includes(j.status)) return j;
        const ev = (await events(id, 1).catch(() => []))[0];
        if (j.status === "queued") say(h("span", { cls: "now" }, macAlive() ? "Queued. The Mac picks it up in a few seconds." : "Queued. It starts when the Mac is back online."));
        else if (j.status === "waiting_quota") say(h("span", { cls: "now" }, `Claude has reached its usage limit. Continues ${j.not_before ? "at " + clock(j.not_before) : "later"}.`));
        else if (ev && ev.id !== lastId) { lastId = ev.id; say(h("span", { cls: "now" }, ev.message), h("span", { cls: "faint small" }, "Usually takes 1–3 minutes")); }
        await sleep(2500);
      }
    } finally { delete S.waiters[id]; }
  })();
}
async function stopRequest(id, btn) {
  if (btn) { btn.disabled = true; btn.textContent = "Stopping…"; }
  try { await rpc("stop_job", { p_job: id }); } catch (e) { toast(errText(e), { err: true }); if (btn) { btn.disabled = false; btn.textContent = "Stop"; } }
}
// ---------------------------------------------------------------- Dashboard: every account at a glance (first page after sign-in)
async function viewDashboard() {
  const grid = h("div", { cls: "accts" }, [1, 2, 3, 4].map(() => h("div", { cls: "skeleton acct-skel" })));
  const strip = h("div", { cls: "dash-strip" });
  shell(h("div", {}, h("div", { cls: "page-head" }, h("div", {}, h("h1", {}, "Dashboard"), h("p", {}, "Every account in one studio. Same process, each with its own reels, posting and autopilot."))), strip, grid));
  const paint = async () => {
    if (!grid.isConnected) return;
    const since = new Date(Date.now() - 30 * 86400e3).toISOString(); const week = Date.now() - 7 * 86400e3;
    const [, { data: rows }, , { data: ap }] = await Promise.all([loadBrands(),
      sb.from("jobs").select("id,brand,status,created_at,updated_at,ig:output->ig->>state,posted:output->ig->>posted_at").eq("kind", "reel").gte("created_at", since).limit(500),
      loadSettings().catch(() => {}),
      sb.from("jobs").select("status,brand,output->autopilot").eq("kind", "suggest").eq("input->>autopilot", "true").order("created_at", { ascending: false }).limit(1)]);
    const all = rows || []; const st = (b) => all.filter((j) => (j.brand || "weone") === b);
    const postedWeek = (list) => list.filter((j) => j.ig === "posted" && j.posted && Date.parse(j.posted) > week).length;
    const apSet = S.settings?.autopilot || {};
    strip.replaceChildren(...[
      h("span", { cls: "pill" }, h("i", { cls: "lamp " + (macAlive() ? "ok" : "bad") }), macAlive() ? "Studio Mac online" : "Studio Mac offline"),
      macAlive() ? h("span", { cls: "pill" }, h("i", { cls: "lamp " + (S.worker?.claude_ok ? "ok" : "bad") }), S.worker?.claude_ok ? "Claude ready" : "Claude not ready") : null,
      h("span", { cls: "pill" }, `${postedWeek(all)} posted this week`),
      apSet.on ? h("span", { cls: "pill" }, `Autopilot daily ${slotLabel(apSet.time || "13:30")}`) : null].filter(Boolean));
    const plat = (b) => {
      const ig = b.id === "weone" ? (S.settings?.instagram_status?.state || "not_connected") : "not_connected";
      const fb = b.id === "weone" ? (S.settings?.facebook_status?.state || "not_connected") : "not_connected";
      const li = b.id === "weone" ? (S.settings?.linkedin_status?.state || "not_connected") : "not_connected";
      const dot = (k, state, label) => h("span", { cls: `mini ${k} ${state === "connected" ? "ok" : state === "not_connected" ? "no" : "warn"}`, title: `${label}: ${state.replace("_", " ")}`, role: "img", "aria-label": `${label}: ${state.replace("_", " ")}` },
        h("img", { src: PLAT_IMG[k], alt: "", width: 24, height: 24 }));
      return h("span", { cls: "minis" }, dot("ig", ig, "Instagram"), dot("fb", fb, "Facebook"), dot("li", li, "LinkedIn"));
    };
    grid.replaceChildren(...[...(S.brands || BRANDS_FALLBACK).map((b) => {
      const list = st(b.id); const live = b.status === "live";
      const nums = [["In production", list.filter((j) => ACTIVE.includes(j.status)).length], ["To review", list.filter((j) => j.status === "review").length], ["Posted · 7 days", postedWeek(list)]];
      const open = () => { setBrand(b.id); go(live && list.some((j) => j.status === "review" || ACTIVE.includes(j.status)) ? "production" : "create"); };
      return h("article", { cls: "acct" + (live ? "" : " setup"), style: `--accent:${b.color || "#0A2342"}` },
        h("button", { cls: "acct-main", onclick: open, "aria-label": `Open ${b.name}` },
          brandLogo(b, 64),
          h("div", { cls: "acct-id" }, h("h3", {}, b.name), h("p", {}, b.tagline || ""),
            h("span", { cls: "acct-state " + (live ? "live" : "setup") }, live ? "Live" : "Setting up"))),
        live ? h("div", { cls: "acct-nums" }, nums.map(([k, v]) => h("div", {}, h("b", { cls: "num" }, v), h("span", {}, k))))
             : h("p", { cls: "acct-next small" }, "Next: content plan, voice and watermark, then its Instagram."),
        h("div", { cls: "acct-foot" }, plat(b),
          safeUrl(b.website) ? h("a", { cls: "acct-web", href: b.website, target: "_blank", rel: "noopener noreferrer" }, h("img", { src: PLAT_IMG.web, alt: "", width: 16, height: 16 }), host(b.website)) : null,
          h("button", { cls: "btn quiet", onclick: () => assetsDialog(b, paint), "aria-label": `Assets of ${b.name}` }, "Assets"),
          h("button", { cls: "btn " + (live ? "primary" : "quiet"), onclick: open }, "Open")));
    }), h("button", { cls: "acct add-acct", onclick: () => addAccountDialog(paint), "aria-label": "Add an account" },
      h("span", { cls: "add-plus", "aria-hidden": "true" }, "+"), h("b", {}, "Add account"),
      h("span", { cls: "small muted" }, "Account Intelligence studies its website, socials, competitors and market, then writes its strategy."))]);
  };
  await paint().catch(() => {});
  S.timers.push(setInterval(() => paint().catch(() => {}), 15000));
}
// ---------------------------------------------------------------- Assets: each account's own logo (used on its reels)
const LOGO_TYPES = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" };
function imgSize(file) {
  return new Promise((res, rej) => { const u = URL.createObjectURL(file); const im = new Image();
    im.onload = () => { res([im.naturalWidth, im.naturalHeight]); URL.revokeObjectURL(u); }; im.onerror = () => { rej(new Error("That file is not an image.")); URL.revokeObjectURL(u); }; im.src = u; });
}
function assetsDialog(b, after) {
  const prev = h("span", { cls: "blogo asset-prev" }, h("img", { src: logoSrc(b), alt: `${b.name} logo`, "data-logo": b.id })); ensureLogo(b);
  const note = h("p", { cls: "small muted" }, b.logo_updated_at ? `Updated ${tzClock(b.logo_updated_at)}` : "Built-in logo");
  const msg = h("p", { cls: "small", role: "status" });
  const input = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", style: "display:none", onchange: async (e) => {
    const f = e.target.files?.[0]; e.target.value = ""; if (!f) return;
    const ext = LOGO_TYPES[f.type];
    if (!ext) { msg.textContent = "Use a PNG, JPG or WebP image."; return; }
    if (f.size > 5 * 1024 * 1024) { msg.textContent = "Keep the logo under 5 MB."; return; }
    let wh; try { wh = await imgSize(f); } catch (err) { msg.textContent = err.message; return; }
    if (Math.min(...wh) < 200) { msg.textContent = `That image is only ${wh[0]}×${wh[1]} px. Use one at least 200 px (1000 px or more looks best).`; return; }
    btn.disabled = true; btn.textContent = "Uploading…"; msg.textContent = "";
    try {
      const path = `${b.id}/logo-${Date.now()}.${ext}`;
      const { error } = await sb.storage.from("brand-assets").upload(path, f, { contentType: f.type, upsert: false }); if (error) throw error;
      await rpc("set_brand_logo", { p_brand: b.id, p_path: path });
      b.logo_asset = path; b.logo_updated_at = new Date().toISOString(); delete S.logoUrls[b.id];
      await loadBrands().catch(() => {}); await ensureLogo(brandOf(b.id));
      note.textContent = `Updated ${tzClock(b.logo_updated_at)}`;
      msg.textContent = Math.max(...wh) < 600 ? "Saved. It's small, so it may look soft on reels; a 1000 px version looks sharper." : "Saved. New reels of this account use it.";
      toast(`${b.name} logo updated.`); after?.();
    } catch (err) { msg.textContent = errText(err); }
    btn.disabled = false; btn.textContent = "Change logo";
  } });
  const btn = h("button", { cls: "btn primary", onclick: () => input.click() }, "Change logo");
  const d = h("dialog", { "aria-label": `${b.name} assets`, cls: "connect plat-dlg" },
    h("div", { style: "padding:18px 18px 4px" },
      h("h3", { style: "margin:0 0 4px" }, `${b.name} · Assets`),
      h("p", { cls: "small muted", style: "margin:0 0 14px" }, "The logo goes on every reel of this account: the corner watermark and the end card. Only this account uses it."),
      h("div", { cls: "asset-row" }, prev, h("div", {}, h("b", {}, "Logo"), note,
        h("p", { cls: "small muted" }, "PNG with a transparent background, square, 1000 px or more looks best. JPG and WebP also work (max 5 MB)."),
        btn, input)), msg),
    h("div", { cls: "dlg-foot" }, h("span"), h("button", { cls: "btn quiet", onclick: () => d.close() }, "Close")));
  d.addEventListener("close", () => d.remove()); document.body.append(d); d.showModal();
}
// ---------------------------------------------------------------- Add account: Account Intelligence starts studying it at once
function addAccountDialog(after) {
  const f = (name, label, ph, type = "text") => h("label", { cls: "fld" }, h("span", {}, label), h("input", { name, type, placeholder: ph, maxlength: "200", autocomplete: "off" }));
  const msg = h("p", { cls: "small", role: "status" });
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault(); const v = Object.fromEntries(new FormData(form)); btn.disabled = true; btn.textContent = "Adding…"; msg.textContent = "";
    try {
      let web = String(v.website || "").trim(); if (web && !/^https?:\/\//i.test(web)) web = "https://" + web;
      const r = await rpc("add_brand", { p_name: v.name, p_website: web, p_instagram: v.instagram || null, p_linkedin: v.linkedin || null, p_facebook: v.facebook || null });
      await loadBrands(); d.close(); toast(`${v.name} added. Account Intelligence is studying it now (about 15-30 min); it opens for reels when the study is done.`, { ms: 10000 });
      after?.(); return r;
    } catch (err) { msg.textContent = errText(err); btn.disabled = false; btn.textContent = "Add and study"; }
  } },
    f("name", "Business name", "e.g. Flying Star Aviator"), f("website", "Website", "https://…"),
    f("instagram", "Instagram (optional)", "@handle or link"), f("linkedin", "LinkedIn (optional)", "https://www.linkedin.com/company/…"),
    f("facebook", "Facebook (optional)", "https://www.facebook.com/…"), msg);
  const btn = h("button", { cls: "btn primary", type: "submit" }, "Add and study"); form.append(btn);
  const d = h("dialog", { "aria-label": "Add account", cls: "connect plat-dlg" },
    h("div", { style: "padding:18px 18px 4px" }, h("h3", { style: "margin:0 0 4px" }, "Add an account"),
      h("p", { cls: "small muted", style: "margin:0 0 12px" }, "Everything stays separate from your other accounts: its knowledge, reels, lessons and posting."), form),
    h("div", { cls: "dlg-foot" }, h("span"), h("button", { cls: "btn quiet", onclick: () => d.close() }, "Close")));
  d.addEventListener("close", () => d.remove()); document.body.append(d); d.showModal(); form.querySelector("input").focus();
}

// ---------------------------------------------------------------- Agents: the team, its health and what it has learned
const TEAMS = [["intelligence", "Intelligence"], ["strategy", "Strategy"], ["production", "Production"], ["quality", "Quality"], ["learning", "Learning"], ["control", "Control"]];
const LESSON_STATE = { active: ["Testing", "wait"], proven: ["Proven", "ok"], rolled_back: ["Rolled back", "bad"], retired: ["Retired", ""] };
async function viewAgents() {
  const body = h("div", {}, h("div", { cls: "skeleton", style: "height:240px" }));
  shell(h("div", {}, h("div", { cls: "page-head" }, h("div", {}, h("h1", {}, "Agents"),
    h("p", {}, `The AI team behind ${brandOf().name}'s reels: who does what, how well it is working, and what it has learned. Lessons apply automatically and roll back on their own if results drop.`))), body));
  const openA = S.agentOpen || (S.agentOpen = {});
  const paint = async () => {
    if (!body.isConnected) return;
    const since = new Date(Date.now() - 14 * 86400e3).toISOString(); const b = S.brand;
    const [ag, runs, les, lrn, onb] = await Promise.all([
      sb.from("agents").select("id,name,role,team,sort").order("sort"),
      sb.from("agent_runs").select("agent,ok,grade,ms,brand,created_at").gte("created_at", since).order("created_at", { ascending: false }).limit(2000),
      sb.from("agent_lessons").select("id,agent,brand,lesson,why,metric,baseline,status,result,created_by,created_at").or(`brand.is.null,brand.eq.${b}`).order("created_at", { ascending: false }).limit(200),
      sb.from("jobs").select("output,created_at,status").eq("kind", "learn").eq("brand", b).order("created_at", { ascending: false }).limit(1),
      sb.from("jobs").select("id,status,output,created_at,updated_at,error").eq("kind", "onboard").eq("brand", b).order("created_at", { ascending: false }).limit(1),
      loadBrands().catch(() => {})]);
    if (ag.error) { body.replaceChildren(h("div", { cls: "panel empty" }, h("p", {}, errText(ag.error)))); return; }
    const R = (runs.data || []).filter((r) => !r.brand || r.brand === b); const L = les.data || [];
    const br = brandOf(); const study = onb.data?.[0]; const learnRun = lrn.data?.[0]?.output?.learn;
    const studying = study && ["queued", "running", "waiting_quota"].includes(study.status);
    const intel = h("section", { cls: "panel agent-intel" },
      h("div", { cls: "ai-head" }, brandLogo(br, 44), h("div", {}, h("h3", {}, "Account Intelligence"),
        h("p", { cls: "small muted" }, studying ? "Studying this account now: website, socials, competitors, market and industry rules…"
          : br.intel_at ? `Last studied ${ago(br.intel_at)}. Re-studied automatically every 30 days.` : "Not studied yet.")),
        h("button", { cls: "btn quiet", disabled: studying, onclick: async (e) => { e.currentTarget.disabled = true;
          try { await rpc("run_onboard", { p_brand: b }); toast(`Account Intelligence will re-study ${br.name} now (about 15-30 min).`); } catch (err) { toast(errText(err), { err: true }); }
          paint(); } }, studying ? "Studying…" : "Re-study now")),
      study?.output?.study ? h("div", { cls: "ai-sum" },
        h("p", {}, study.output.study.strategy || ""),
        h("div", { cls: "chips" }, h("span", { cls: "pill" }, `${study.output.study.topics} topics researched`), h("span", { cls: "pill" }, `Profile v${study.output.study.version}`),
          (study.output.study.competitors || []).length ? h("span", { cls: "pill" }, `Competitors: ${study.output.study.competitors.slice(0, 4).join(", ")}`) : null)) : null,
      study?.status === "failed" ? h("p", { cls: "small err" }, study.error || "The last study failed.") : null);
    const learnBox = learnRun ? h("section", { cls: "panel" }, h("h3", {}, "Latest learning run"),
      learnRun.insights ? h("p", {}, learnRun.insights) : null, learnRun.usage_note ? h("p", { cls: "small muted" }, "Resources: " + learnRun.usage_note) : null,
      (learnRun.evaluated || []).length ? h("ul", { cls: "small" }, learnRun.evaluated.map((x) => h("li", {}, x))) : null) : null;
    const card = (a) => {
      const rs = R.filter((r) => r.agent === a.id); const okN = rs.filter((r) => r.ok).length; const g = rs.filter((r) => r.grade != null);
      const rate = rs.length ? okN / rs.length : null; const lamp = rate == null ? "" : rate >= 0.9 ? "ok" : rate >= 0.7 ? "wait" : "bad";
      const mine_ = L.filter((l) => l.agent === a.id); const live = mine_.filter((l) => l.status === "active" || l.status === "proven");
      const stats = rs.length ? [`${rs.length} runs`, `${Math.round(100 * rate)}% ok`, g.length ? `grade ${Math.round(g.reduce((x, r) => x + Number(r.grade), 0) / g.length)}` : null,
        `last ${ago(rs[0].created_at)}`].filter(Boolean).join(" · ") : "No runs in the last 14 days";
      const det = openA[a.id] ? h("div", { cls: "lessons" }, mine_.length ? mine_.map((l) => { const [w, c] = LESSON_STATE[l.status] || [l.status, ""];
        return h("div", { cls: "lesson" }, h("span", { cls: `lstate ${c}` }, w), h("div", {}, h("p", {}, l.lesson),
          h("p", { cls: "small muted" }, [l.why, l.brand ? null : "all accounts", `${l.created_by.replace("_", " ")} · ${ago(l.created_at)}`,
            l.result ? `${l.metric}: ${l.result.baseline} → ${l.result.after} (${l.result.n} results)` : `measured on ${l.metric}`].filter(Boolean).join(" · "))));
      }) : h("p", { cls: "small muted" }, "No lessons yet. They appear after the first learning runs.")) : null;
      return h("article", { cls: "agent" }, h("button", { cls: "agent-main", "aria-expanded": String(!!openA[a.id]), onclick: () => { openA[a.id] = !openA[a.id]; paint(); } },
        h("div", { cls: "agent-top" }, h("i", { cls: "lamp " + lamp }), h("b", {}, a.name), live.length ? h("span", { cls: "pill" }, `${live.length} lesson${live.length > 1 ? "s" : ""}`) : null),
        h("p", { cls: "small" }, a.role), h("p", { cls: "small muted" }, stats)), det);
    };
    const groups = TEAMS.map(([k, label]) => { const as = (ag.data || []).filter((a) => a.team === k); return as.length ? h("section", { cls: "team" }, h("h3", {}, label), h("div", { cls: "agents" }, as.map(card))) : null; });
    if (body.isConnected) body.replaceChildren(intel, learnBox || "", ...groups.filter(Boolean));
  };
  await paint().catch((e) => body.replaceChildren(h("div", { cls: "panel empty" }, h("p", {}, errText(e)))));
  S.timers.push(setInterval(() => paint().catch(() => {}), 20000));
}

function setupCard() {
  const b = brandOf();
  const steps = [["Content plan", "Topics, audience and what a viral reel looks like for " + b.name + "."], ["Voice & style", "Narrator voice, Hinglish mix, captions and the call to action."],
    ["Watermark", "Logo placement on every reel."], ["Instagram", b.instagram ? `Connect @${b.instagram} for automatic posting.` : "Connect its Instagram account for automatic posting."]];
  steps[0] = ["Account Intelligence study", "Its website, socials, services, competitors, market trends and industry rules become its knowledge profile and strategy."];
  return h("section", { cls: "panel setupcard" }, h("div", { style: "display:flex;gap:14px;align-items:center" }, brandLogo(b, 56),
      h("div", {}, h("h3", {}, `${b.name} is being set up`), h("p", { cls: "small muted" }, "Account Intelligence studies it first; reels open here as soon as the study is done (about 15-30 min)."))),
    h("ol", { cls: "steps", style: "margin-top:14px" }, steps.map(([t, x]) => h("li", {}, h("b", {}, t), h("span", {}, x)))),
    h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:6px" },
      h("button", { cls: "btn primary", onclick: async (e) => { e.currentTarget.disabled = true; try { await rpc("run_onboard", { p_brand: b.id }); toast(`Account Intelligence is studying ${b.name}.`); } catch (err) { toast(errText(err), { err: true }); } } }, "Study it now"),
      h("button", { cls: "btn quiet", onclick: () => go("agents") }, "See the agents"),
      h("button", { cls: "btn quiet", onclick: () => go("home") }, "Back to Dashboard")));
}

// ---------------------------------------------------------------- Autopilot: one viral-ready reel a day, no taps
function nextRun(ap) {
  const [hh, mm] = String(ap.time || "13:30").split(":").map(Number);
  const nowIst = new Date(Date.now() + 330 * 60e3); const t = new Date(Date.UTC(nowIst.getUTCFullYear(), nowIst.getUTCMonth(), nowIst.getUTCDate(), hh, mm) - 330 * 60e3);
  return t.getTime() + 3 * 3600e3 < Date.now() || (S.apLast?.input?.day === nowIst.toISOString().slice(0, 10) && S.apLast?.input?.trigger === "schedule") ? new Date(t.getTime() + 86400e3) : t;
}
function autopilotPanel() {
  const box = h("section", { cls: "panel ap", "aria-label": "Autopilot" });
  const paint = async () => {
    if (!box.isConnected && box._painted) return;
    const [{ data }] = await Promise.all([sb.from("jobs").select("id,status,input,output,error,updated_at,created_at").eq("kind", "suggest").eq("input->>autopilot", "true").order("created_at", { ascending: false }).limit(1), loadSettings().catch(() => {})]);
    const j = (data || [])[0]; S.apLast = j; box._painted = true;
    const ap = S.settings?.autopilot || { on: false, time: "13:30" }; const ins = S.settings?.instagram_insights;
    const ed = S.apEdit; const o = j?.output || {}; const pick = o.autopilot;
    let lastLine = null, ev = null;
    if (j && ACTIVE.includes(j.status)) { ev = (await events(j.id, 1).catch(() => []))[0];
      lastLine = h("p", { cls: "small", style: "margin-top:8px" }, h("i", { cls: "lamp wait" }), j.status === "queued" ? (macAlive() ? "Starting…" : "Queued. It starts when the Mac is online.") : j.status === "waiting_quota" ? `Claude usage limit; continues ${j.not_before ? "at " + clock(j.not_before) : "later"}.` : (ev?.message || "Researching today's trends…")); }
    else if (j && j.status === "done" && pick?.skipped) lastLine = h("p", { cls: "small muted", style: "margin-top:8px" }, `The ${pick.day} run was skipped: the Mac was offline all afternoon.`);
    else if (j && j.status === "done" && pick) lastLine = h("div", { cls: "ap-pick" },
      h("p", { cls: "tiny faint" }, `Last pick, ${ago(j.updated_at)} · score ${pick.score}/100 · ${String(pick.category || "").replace("_", " ")}`),
      h("h3", {}, pick.title), h("p", { cls: "hook" }, pick.hook), pick.why ? h("p", { cls: "small muted" }, pick.why) : null,
      h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:8px" }, h("button", { cls: "btn", onclick: () => go("production", pick.reel_id) }, "Follow this reel"),
        (o.topics || []).length ? h("details", { cls: "more" }, h("summary", {}, `All ${o.topics.length} topics it scored`), h("ol", { cls: "ap-board" }, o.topics.map((t) =>
          h("li", {}, h("b", { cls: "num" }, t.score == null ? (t.repeat ? "repeat" : "unsafe") : `${t.score}`), " ", t.title, t.editor_note ? h("span", { cls: "tiny faint" }, ` · ${t.editor_note}`) : null)))) : null));
    else if (j && ["failed", "cancelled"].includes(j.status)) lastLine = h("p", { cls: "small", style: "margin-top:8px;color:#7D1C26" }, `Last run ${j.status === "failed" ? "failed" : "was stopped"}: ${j.error || ""}`);
    const timeIn = h("input", { type: "time", value: ed?.time || ap.time || "13:30", "aria-label": "Daily time (India)", style: "width:130px", oninput: (e) => { S.apEdit = { ...(S.apEdit || ap), time: e.target.value }; } });
    const save = async (on, time) => { try { await rpc("set_autopilot", { p_on: on, p_time: time }); S.apEdit = null; toast(on ? `Autopilot on: every day at ${slotLabel(time)}.` : "Autopilot off."); } catch (e) { toast(errText(e), { err: true }); } paint(); };
    const running = j && ACTIVE.includes(j.status);
    box.replaceChildren(
      h("div", { cls: "igp-head" },
        h("div", {}, h("h3", {}, h("i", { cls: "lamp " + (ap.on ? "ok" : "") }), ap.on ? `Autopilot is on: every day at ${slotLabel(ap.time || "13:30")}` : "Autopilot is off"),
          h("p", { cls: "small muted", style: "margin-top:4px" }, "Picks today's most viral-ready topic, makes the reel and posts it. No taps needed."),
          ap.on && !running ? h("p", { cls: "tiny faint", style: "margin-top:4px" }, `Next run ${tzClock(nextRun(ap))}.`) : null,
          ins && ins.ok === false ? h("p", { cls: "tiny", style: "margin-top:4px;color:#6E4300" }, "Learning uses likes and comments only. Add the instagram_business_manage_insights permission to the Instagram token for views, sends and saves.") : null),
        h("div", { cls: "igp-btns" },
          ed ? [timeIn, h("button", { cls: "btn primary", onclick: () => save(true, timeIn.value || "13:30") }, "Save"), h("button", { cls: "btn quiet", onclick: () => { S.apEdit = null; paint(); } }, "Cancel")]
             : [h("button", { cls: "btn primary", disabled: !!running, onclick: async (e) => { e.currentTarget.disabled = true; try { await rpc("run_autopilot_now", {}); toast("Autopilot started. It picks a topic in a few minutes, then makes the reel."); } catch (err) { toast(errText(err), { err: true }); } paint(); } }, running ? "Running…" : "Run now"),
                h("button", { cls: "btn quiet", onclick: () => { S.apEdit = { ...ap }; paint(); } }, "Change time"),
                h("button", { cls: "btn quiet", onclick: () => save(!ap.on, ap.time || "13:30") }, ap.on ? "Turn off" : "Turn on")])),
      lastLine || "");
  };
  paint().catch(() => {});
  const iv = setInterval(() => { if (!box.isConnected && box._painted) return clearInterval(iv); if (!box.contains(document.activeElement)) paint().catch(() => {}); }, 8000);
  S.timers.push(iv);
  return box;
}
function viewCreate() {
  const seg = h("div", { cls: "seg", role: "group", "aria-label": "How to choose a topic" },
    h("button", { "aria-pressed": String(S.mode === "suggest"), onclick: () => { S.mode = "suggest"; S.picked = null; viewCreate(); } }, "Suggest topics"),
    h("button", { "aria-pressed": String(S.mode === "mine"), onclick: () => { S.mode = "mine"; S.picked = S.concept; viewCreate(); } }, "Use my idea"));
  const left = h("div"); const plan = h("aside", { cls: "panel plan", "aria-label": "Flight plan" });
  shell(h("div", { cls: "with-plan" },
    h("div", { cls: "page-head" }, h("div", {}, h("h1", {}, "Create a reel"), h("p", {}, "Choose a topic. You'll see when the reel will be ready before anything starts.")), seg),
    S.brand === "weone" ? autopilotPanel() : null,
    h("div", { cls: "create" }, left, plan)));
  if (!isLive()) { $app.querySelector(".create").replaceChildren(setupCard()); $app.querySelector(".page-head .seg")?.remove(); return; }
  const paintPlan = () => {
    const t = S.picked; const e = etaNew(); const w = S.worker; const capHit = w?.info && w.info.reels_today >= w.info.daily_cap;
    const go_ = h("button", { cls: "btn go block", disabled: !t, onclick: async (ev) => {
      ev.currentTarget.disabled = true; askAlerts();
      try { const j = await createJob("reel", { concept: t, source: S.mode });
        toast(Date.now() - new Date(j.created_at) > 60e3 ? "This topic is already in production. Showing it." : "Reel queued. Follow it in Production.");
        S.picked = null; S.concept = null; go("production", j.id); }
      catch (err) { toast(errText(err), { err: true }); ev.currentTarget.disabled = false; }
    } }, "Create reel");
    if (!t) { plan.replaceChildren(h("h3", {}, "Flight plan"), h("p", { cls: "plan-empty small" }, "Pick a topic to see when it will be ready."), h("div", { cls: "plan-row" }, h("span", { cls: "faint small" }, `Typical reel: ${mins(totalSec())}`), go_)); return; }
    plan.replaceChildren(
      h("h3", {}, t.title),
      h("div", { cls: "plan-row" }, h("div", {}, h("div", { cls: "tiny muted" }, "Ready around"), h("div", { cls: "eta num" }, clock(e.at), h("small", {}, mins(e.left)))), go_),
      h("div", { cls: "legs", "aria-hidden": "true" }, STAGES.map((st) => h("i", { style: `flex:${S.est.sec[st]}` }))),
      h("div", { cls: "leg-list" }, STAGES.map((st) => [h("span", {}, STAGE_NAME[st]), h("span", { cls: "num" }, shortMins(S.est.sec[st]))])),
      h("div", { cls: "plan-extra small muted" },
        e.ahead ? h("p", {}, `${e.ahead} reel${e.ahead > 1 ? "s" : ""} ahead of this one; included in the time above.`) : null,
        !macAlive() ? h("p", { style: "color:var(--danger)" }, "The Mac is offline. The reel waits in the queue until it's back.") : null,
        w && w.claude_ok === false ? h("p", { style: "color:#6E4300" }, `Claude: ${w.claude_note || "not ready"}`) : null,
        capHit ? h("p", { style: "color:#6E4300" }, "Today's limit is reached; this reel starts after midnight.") : null,
        h("p", { cls: "faint tiny", style: "margin-top:8px" }, S.est.basis ? `Based on the last ${S.est.basis} reels made on this Mac.` : "First estimate; it gets more precise after a few reels.")));
  };
  const pick = (t) => { S.picked = t; left.querySelectorAll(".topic").forEach((b) => { b.setAttribute("aria-checked", String(b._t === t)); if (b.classList.contains("compact")) b.setAttribute("aria-expanded", String(b._t === t)); }); paintPlan(); };
  paintPlan();

  if (S.mode === "suggest") {
    const steer = h("input", { placeholder: "Optional: steer the ideas, e.g. cockpit secrets, famous landings", "aria-label": "Steer the ideas", value: S.steer, oninput: (e) => { S.steer = e.target.value; } });
    const genBtn = h("button", { cls: "btn primary", onclick: () => gen() }, "Generate suggestions");
    const list = h("div", { cls: "topics", role: "radiogroup", "aria-label": "Suggested topics" });
    const head = h("div", { cls: "panel", style: "margin-bottom:14px" }, h("h3", {}, "Researched topics for " + brandOf().name),
      h("p", { cls: "muted small", style: "margin:4px 0 14px" }, "Half from this account's own expertise, half from what its audience talks about now. Every topic is backed by sources."),
      h("div", { style: "display:flex;gap:10px;flex-wrap:wrap" }, h("div", { style: "flex:1 1 260px" }, steer), genBtn));
    left.replaceChildren(head, list);
    const show = (j, when) => {
      const topics = j.output?.topics || []; const ln = j.output?.lane_names || { core: "Core", trending: "Trending" }; const mx = j.output?.mix;
      const mixLine = mx && mx.window ? ` · last ${mx.window} reels: ${mx.core} ${ln.core.toLowerCase()}, ${mx.trending} trending, next: ${(ln[mx.next] || mx.next).toLowerCase()}` : "";
      list.replaceChildren(h("p", { cls: "faint small", style: "margin:0 4px" }, `Suggested ${ago(when)}${mixLine}`), ...topics.map((t) => {
        // compact by default: title + hook; tapping a topic picks it and opens its details (one open at a time)
        const b = h("div", { cls: "topic compact", role: "radio", tabindex: "0", "aria-checked": String(S.picked === t), "aria-expanded": String(S.picked === t),
            onclick: () => pick(t), onkeydown: (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); pick(t); } } },
          h("span", { cls: "radio", "aria-hidden": "true" }), t.lane ? h("span", { cls: "lane " + t.lane }, ln[t.lane] || t.lane) : null, h("h3", {}, t.title), h("p", { cls: "hook" }, t.hook),
          h("div", { cls: "topic-more", onclick: (e) => { if (e.target.closest("a, summary")) e.stopPropagation(); } },
            h("div", { cls: "kv" }, t.angle ? h("span", {}, h("b", {}, "Angle"), t.angle) : null, t.why_viral ? h("span", {}, h("b", {}, "Why it travels"), t.why_viral) : null,
              Number(t.est_seconds) > 0 ? h("span", {}, h("b", {}, "Length"), `${t.est_seconds} s`) : null),
            (t.key_facts || []).length || (t.sources || []).length ? h("details", { cls: "more" }, h("summary", {}, "Facts and sources"),
              h("ul", {}, (t.key_facts || []).map((f) => h("li", {}, f))), h("div", { cls: "sources" }, (t.sources || []).map((x) => srcChip(x.url, x.title)))) : null,
            t.risk_note ? h("p", { cls: "note" }, t.risk_note) : null),
          h("span", { cls: "topic-hint tiny faint" }, "Tap for details"));
        b._t = t; return b;
      }), topics.length ? h("button", { cls: "btn quiet", onclick: () => gen() }, "Suggest five different topics") : null);
      if (S.picked) pick(topics.find((x) => x.title === S.picked.title) || null);
    };
    const failBox = (msg) => h("div", { cls: "banner err", style: "justify-content:space-between;align-items:center;flex-wrap:wrap" }, h("span", {}, msg),
      h("button", { cls: "btn", onclick: () => gen() }, "Try again"));
    async function follow(id) {     // shows a request in progress (also after leaving and coming back, or a page reload)
      genBtn.disabled = true; S.picked = null; paintPlan();
      const ticker = h("div", { cls: "ticker" }, h("span", { cls: "now" }, "Waiting for the Mac…"));
      list.replaceChildren(thinkingBox(ticker, id));
      const d = await waitJob(id, ticker);
      if (S.pending.suggest === id) { delete S.pending.suggest; savePending(); }
      if (!list.isConnected) return;
      if (d.status === "done") { S.lastSuggest = d; show(d, d.updated_at); }
      else if (d.status === "cancelled") list.replaceChildren(failBox("Stopped. Nothing was changed."));
      else list.replaceChildren(failBox(`Suggestions failed: ${d.error || d.status}.`));
      genBtn.disabled = false;
    }
    async function gen() {
      if (genBtn.disabled && S.pending.suggest) return;
      genBtn.disabled = true; list.replaceChildren(thinkingBox(h("div", { cls: "ticker" }, h("span", { cls: "now" }, "Sending the request to the Mac…"))));
      try {
        const recent = S.lastSuggest?.output?.topics?.map((t) => t.title) || [];
        const j = await createJob("suggest", { note: steer.value.trim(), avoid: recent });
        S.pending.suggest = j.id; savePending(); await follow(j.id);
      } catch (e) { list.replaceChildren(failBox(errText(e))); genBtn.disabled = false; }
    }
    (async () => {
      if (S.pending.suggest) return follow(S.pending.suggest);
      if (!S.lastSuggest) { const { data } = await sb.from("jobs").select("*").eq("kind", "suggest").eq("status", "done").eq("brand", S.brand).is("input->autopilot", null).order("created_at", { ascending: false }).limit(1); S.lastSuggest = data?.[0]; }
      if (!list.isConnected) return;
      if (S.lastSuggest && Date.now() - new Date(S.lastSuggest.updated_at) < 86400e3) show(S.lastSuggest, S.lastSuggest.updated_at);
      else list.replaceChildren(h("div", { cls: "panel empty" }, routeArt("chart"), h("h3", {}, "No suggestions yet"), h("p", {}, "Generate five ideas; it usually takes one to three minutes.")));
    })();
  } else {
    const ta = h("textarea", { id: "idea", maxlength: "2000", placeholder: "For example: why do pilots and co-pilots eat different meals?", oninput: (e) => { S.idea = e.target.value; } });
    ta.value = S.idea;
    const goBtn = h("button", { cls: "btn primary", onclick: () => opt() }, "Shape my idea");
    const out = h("div");
    left.replaceChildren(h("div", { cls: "panel", style: "margin-bottom:14px" }, h("h3", {}, "Your topic"),
      h("p", { cls: "muted small", style: "margin:4px 0 12px" }, "Write it in Hindi, Hinglish or English. Claude checks the facts and turns it into a reel concept for you to confirm."),
      h("label", { cls: "sr", for: "idea" }, "Your topic"), ta, h("div", { style: "margin-top:12px" }, goBtn)), out);
    const showConcept = (c) => {
      const fb = h("input", { placeholder: "What should change? e.g. stronger hook, focus on the Indian context" , "aria-label": "Feedback for a new version" });
      out.replaceChildren(h("div", { cls: "topic", "aria-checked": "true", style: "cursor:default" }, h("span", { cls: "radio", "aria-hidden": "true" }),
        h("h3", {}, c.title), h("p", { cls: "hook" }, c.hook), h("p", { style: "margin-top:10px" }, c.concept),
        h("p", { cls: "small", style: "margin-top:12px;font-weight:600" }, "Outline"), h("ol", { cls: "small", style: "margin:4px 0 0;padding-left:20px" }, (c.outline || []).map((x) => h("li", {}, x))),
        h("details", { cls: "more" }, h("summary", {}, "Facts and sources"), h("ul", {}, (c.key_facts || []).map((f) => h("li", {}, f))), h("div", { cls: "sources" }, (c.sources || []).map((x) => srcChip(x.url, x.title)))),
        c.notes_for_user ? h("p", { cls: "note" }, c.notes_for_user) : null),
        h("div", { cls: "panel", style: "margin-top:12px;display:flex;gap:10px;flex-wrap:wrap;align-items:center" }, h("div", { style: "flex:1 1 240px" }, fb),
          h("button", { cls: "btn", onclick: () => opt(c, fb.value.trim()) }, "Make a new version")));
      S.concept = c; S.picked = c; paintPlan();
    };
    let lastArgs = [];
    const failBox = (msg) => h("div", { cls: "banner err", style: "justify-content:space-between;align-items:center;flex-wrap:wrap" }, h("span", {}, msg),
      h("button", { cls: "btn", onclick: () => opt(...lastArgs) }, "Try again"));
    async function follow(id) {
      goBtn.disabled = true; S.picked = null; paintPlan();
      const ticker = h("div", { cls: "ticker" }, h("span", { cls: "now" }, "Waiting for the Mac…"));
      out.replaceChildren(thinkingBox(ticker, id));
      const d = await waitJob(id, ticker);
      if (S.pending.optimize === id) { delete S.pending.optimize; savePending(); }
      if (d.status === "done" && d.output?.concept) S.concept = d.output.concept;
      if (!out.isConnected) return;
      if (d.status === "done" && d.output?.concept) showConcept(d.output.concept);
      else if (d.status === "cancelled") out.replaceChildren(failBox("Stopped. Nothing was changed."));
      else out.replaceChildren(failBox(`That didn't work: ${d.error || d.status}.`));
      goBtn.disabled = false;
    }
    async function opt(prev, feedback) {
      if (!ta.value.trim()) { ta.focus(); toast("Write your topic first.", { err: true }); return; }
      lastArgs = [prev, feedback];
      goBtn.disabled = true; out.replaceChildren(thinkingBox(h("div", { cls: "ticker" }, h("span", { cls: "now" }, "Sending your idea to the Mac…"))));
      try {
        const j = await createJob("optimize", { prompt: ta.value.trim(), previous: prev || null, feedback: feedback || "" });
        S.pending.optimize = j.id; savePending(); await follow(j.id);
      } catch (e) { out.replaceChildren(failBox(errText(e))); goBtn.disabled = false; }
    }
    if (S.pending.optimize) follow(S.pending.optimize); else if (S.concept) showConcept(S.concept);
  }
}

// ---------------------------------------------------------------- production
function stageState(j, st) {
  const t = j.output?.timings || {};
  if (t[st] != null) return "done";
  if (j.status === "running" && j.stage === st) return "now";
  if (j.status === "review") return "done";
  return "todo";
}
function routeMap(j) {
  const idx = STAGES.findIndex((st) => stageState(j, st) === "now"); const doneN = STAGES.filter((st) => stageState(j, st) === "done").length;
  const t = j.output?.timings || {}; const started = j.output?.stage_started || {};
  let frac = 0;
  if (idx >= 0 && started[STAGES[idx]]) frac = Math.min(0.92, (Date.now() / 1000 - started[STAGES[idx]]) / S.est.sec[STAGES[idx]]);
  const pos = idx >= 0 ? idx + frac : Math.max(0, doneN - 1);
  const pct = (p) => `calc(${(100 / 12) + (p * (100 - 100 / 6)) / 5}%)`;
  const sel = S.sel[j.id] || STAGES[idx >= 0 ? idx : Math.min(doneN, 5)];
  const plane = s("svg", { class: "plane", viewBox: "0 0 24 24", style: `left:${pct(pos)}`, "aria-hidden": "true" }, s("path", { d: ICON.plane, fill: "var(--route)" }));
  return h("div", { cls: "route" },
    h("div", { cls: "line" }), h("div", { cls: "flown", style: `width:calc(${(pos * (100 - 100 / 6)) / 5}%)` }), j.status === "running" ? plane : null,
    h("ol", {}, STAGES.map((st) => {
      const state = stageState(j, st);
      const time = state === "done" && t[st] != null ? shortMins(t[st]) : state === "now" ? "now" : shortMins(S.est.sec[st]);
      return h("li", { cls: state === "done" ? "past" : "" }, h("button", { cls: "wp-btn " + state, "aria-expanded": String(sel === st), title: STAGE_HELP[st],
        onclick: () => { S.sel[j.id] = st; refreshProduction(); } },
        h("span", { cls: "wp-dot" }, state === "done" ? icon("check", 16) : null), h("span", {}, STAGE_NAME[st]), h("span", { cls: "wp-time" }, time)));
    })));
}
function graph(j, st) {
  const trace = j.output?.trace?.[st] || {}; const state = stageState(j, st);
  let nodes = Object.entries(trace).map(([id, n]) => ({ id, ...n }));
  if (j.status !== "running") nodes = nodes.map((n) => n.status === "run" ? { ...n, status: "wait", info: ACTIVE.includes(j.status) ? "continues here" : "stopped here" } : n);
  if (!nodes.length) nodes = DEFAULT_NODES[st].map(([id, label]) => ({ id, label, status: state === "done" ? "done" : "wait", info: "" }));
  const small = innerWidth < 600, W = small ? 330 : 520, X = small ? 128 : 238, HW = small ? 78 : 96, MAXC = small ? 22 : 38;
  const n = nodes.length, rowH = 46, H = Math.max(170, n * rowH + 30), hubY = H / 2;
  const cls = (x) => x === "done" ? "done" : x === "run" ? "run" : x === "fail" ? "fail" : "";
  const hx = 6 + HW, mid = (hx + X) / 2;
  return h("div", { cls: "graph", role: "img", "aria-label": `${STAGE_NAME[st]} steps` }, s("svg", { viewBox: `0 0 ${W} ${H}` },
    nodes.map((nd, i) => { const y = 15 + rowH / 2 + i * rowH; return s("path", { class: "edge " + cls(nd.status), d: `M${hx} ${hubY} C ${mid} ${hubY}, ${mid} ${y}, ${X - 8} ${y}` }); }),
    s("rect", { class: "hub", x: 6, y: hubY - 22, width: HW, height: 44, rx: 12 }),
    s("text", { class: "hub-label", x: 6 + HW / 2, y: hubY + 5, "text-anchor": "middle" }, STAGE_NAME[st]),
    nodes.map((nd, i) => {
      const y = 15 + rowH / 2 + i * rowH;
      const lab = String(nd.label); 
      return s("g", {}, nd.status === "run" && !reduced ? s("circle", { class: "halo", cx: X, cy: y, r: 9 }) : null,
        s("circle", { class: "node " + cls(nd.status), cx: X, cy: y, r: 9 }),
        s("text", { class: "lbl", x: X + 20, y: y - 2 }, lab.length > MAXC ? lab.slice(0, MAXC - 1) + "…" : lab),
        s("text", { class: "sub", x: X + 20, y: y + 14 }, nd.info || (nd.status === "wait" ? "waiting" : nd.status === "run" ? "working" : nd.status === "fail" ? "skipped" : "done")));
    })));
}
async function liveLog(j) {
  const ev = await events(j.id, 12);
  const clean = ev.filter((e) => !/^stage \w+ (started|done)/.test(e.message));
  return h("div", { cls: "log" }, h("h4", {}, h("i", { cls: "lamp " + (j.status === "running" ? "wait" : "ok") }), "Live log"),
    clean.length ? h("ol", {}, clean.map((e) => h("li", { cls: e.level }, h("span", { cls: "num" }, clock(e.ts)), h("span", {}, e.message)))) : h("p", { cls: "muted small" }, "Updates appear here as the Mac works."));
}
async function strip(j) {
  const e = etaFor(j); const open = S.open[j.id] ?? ["running", "review", "failed", "cancelled"].includes(j.status);   // stopped reels open so Resume is right there
  const blocked = S.worker?.info?.blocked, capHit = S.worker?.info?.cap_hit;
  const retrying = j.status === "queued" && j.error;
  const status = stopping(j) ? "Stopping on the Mac… finished steps are kept. Resume becomes available in a few seconds."
    : S.busy[j.id] === "stop" ? "Stopping…"
    : !macAlive() && ACTIVE.includes(j.status) ? "Paused: the studio Mac is offline. It continues from where it stopped when the Mac is back."
    : retrying ? `${j.error}${j.not_before && new Date(j.not_before) > Date.now() ? ` Next try at ${clock(j.not_before)}.` : ""}`
    : j.status === "queued" && blocked ? `Waiting: the Mac has ${blocked}. Free some space and it starts by itself.`
    : j.status === "queued" && capHit && !j.parent_id ? "Waiting: today's reel limit is reached. Starts after midnight."
    : { queued: "Waiting for its turn", running: `${STAGE_NAME[j.stage] || "Starting"}: ${STAGE_HELP[j.stage] || ""}`,
        waiting_quota: `Paused: Claude usage limit. Continues ${j.not_before ? "at " + clock(j.not_before) : "later"}.`, review: autoPostLine(j),
        failed: j.error || "Stopped by an error. Saved progress is kept.", cancelled: "Stopped. Finished steps are kept; press Resume to continue from where it stopped." }[j.status];
  const right = j.status === "review" ? h("div", { cls: "strip-eta" }, h("div", { cls: "tiny" }, "Finished"), h("div", { cls: "big" }, clock(j.updated_at)))
    : ["failed", "cancelled"].includes(j.status) ? h("div", { cls: "strip-eta" }, h("div", { cls: "tiny" }, j.status === "failed" ? "Failed" : "Stopped"), h("div", { cls: "big" }, clock(j.updated_at)))
    : h("div", { cls: "strip-eta" }, h("div", { cls: "tiny" }, "ETA"), h("div", { cls: "big" }, clock(e.at)), h("div", { cls: "tiny" }, mins(e.left)));
  const el = h("article", { cls: "strip " + j.status, "aria-label": titleOf(j) },
    h("div", { cls: "strip-head", role: "button", tabindex: "0", "aria-expanded": String(open), onclick: () => { S.open[j.id] = !open; refreshProduction(); },
      onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); S.open[j.id] = !open; refreshProduction(); } } },
      h("div", { cls: "callsign" }, callsign(j.id), h("small", {}, dateShort(j.created_at))),
      h("div", { cls: "strip-title" }, h("h3", {}, titleOf(j)), h("p", { cls: "muted" }, status)), right));
  if (!open) return el;
  if (j.status === "review") { el.append(h("div", { cls: "strip-body" }, await reviewView(j))); return el; }
  const sel = S.sel[j.id] || (j.stage && STAGES.includes(j.stage) ? j.stage : "script");
  const body = h("div", { cls: "strip-body" }, routeMap(j),
    h("div", { cls: "stage-detail" }, h("div", {}, h("p", { cls: "small muted", style: "margin:0 0 8px" }, STAGE_HELP[sel]), graph(j, sel)), await liveLog(j)));
  body.append(actions(j)); el.append(body); return el;
}
const ownPosting = (j) => ((j && j.brand) || S.brand) === "weone";          // only We One has its own connected platforms today
const autoOn = (j) => ownPosting(j) && S.settings?.post_schedule?.auto !== false;
const nightIST = () => { const d = new Date(Date.now() + 330 * 60e3); const m = d.getUTCHours() * 60 + d.getUTCMinutes(); return m < 450 || m > 1380; };   // 23:00-07:30 India time
const nightWait = (j) => j.input?.autopilot && nightIST();
function autoPostLine(j) {
  const d = j.output?.review_deadline;
  if (j.output?.autopilot_hold) return "Ready. Autopilot held it back: approve it to post.";
  if (autoOn(j) && nightWait(j)) return "Ready. Autopilot posts it at 7:30 AM (never at night) unless you act.";
  if (!ownPosting(j)) return `Ready for your review. It goes to the Library only: ${brandOf(j.brand).name} has no Instagram connected yet.`;
  if (!autoOn(j) || !d) return "Ready for your review";
  if (j.output?.hold) return "Ready for review. Auto-post paused while you check.";
  const left = (Date.parse(d) - Date.now()) / 1000;
  return left > 0 ? `Ready. Posts to Instagram automatically at ${clock(d)} unless you act.` : "Ready. Posting to Instagram now…";
}
// Buttons keep their state in S (not in the DOM), so the 5-second refresh can never reset a half-done tap.
async function act(j, kind, fn, okMsg) {
  if (S.busy[j.id]) return;
  S.busy[j.id] = kind; delete S.confirm[j.id]; refreshProduction();
  try { const row = await fn(); patchLocal(row); if (okMsg) toast(typeof okMsg === "function" ? okMsg(row) : okMsg); return row; }
  catch (e) { toast(errText(e), { err: true, ms: 8000 }); }
  finally { delete S.busy[j.id]; try { await poll(); } catch { /* */ } refreshProduction(); }
}
function actions(j) {
  const acts = h("div", { cls: "strip-actions" }); const busy = S.busy[j.id];
  if (ACTIVE.includes(j.status)) {
    if (busy === "stop") acts.append(h("button", { cls: "btn quiet danger", disabled: true }, "Stopping…"));
    else if ((S.confirm[j.id] || 0) > Date.now()) acts.append(
      h("span", { cls: "small muted", style: "align-self:center" }, "Stop this reel? Finished steps are kept, so you can resume it later."),
      h("button", { cls: "btn", onclick: () => { delete S.confirm[j.id]; refreshProduction(); } }, "Keep going"),
      h("button", { cls: "btn danger", onclick: () => act(j, "stop", () => rpc("stop_job", { p_job: j.id }), "Stopped. Finished steps are kept; press Resume any time.") }, "Yes, stop"));
    else acts.append(h("button", { cls: "btn quiet danger", onclick: () => { S.confirm[j.id] = Date.now() + 10000; refreshProduction(); setTimeout(() => refreshProduction(), 10100); } }, "Stop reel"));
  }
  if (["failed", "cancelled"].includes(j.status)) {
    const wait = stopping(j);
    acts.append(h("button", { cls: "btn primary", disabled: !!busy || wait, onclick: () => act(j, "resume", () => rpc("retry_job", { p_job: j.id }),
      `Resuming from ${STAGE_NAME[j.stage] || "the last saved step"}. Finished steps are kept.`) }, busy === "resume" ? "Resuming…" : wait ? "Stopping…" : "Resume"));
  }
  return acts;
}
async function reviewView(j) {
  const o = j.output || {}; const name = `${titleOf(j).replace(/[^\w\- ]+/g, "").trim().slice(0, 50) || "reel"}.mp4`;
  const [url, poster, dl] = await Promise.all([signed(o.video), signed(o.thumb), signed(o.video, name)]);
  const video = url ? h("video", { src: url, poster, controls: true, playsinline: true, preload: "metadata" }) : h("p", { cls: "muted", style: "color:#fff;padding:20px" }, "Video not available yet. It appears here once the upload finishes.");
  if (url) video.addEventListener("error", async () => { if ((video._re = (video._re || 0) + 1) > 3) return; const t = video.currentTime; const u = await signed(o.video); if (u) { video.src = u; video.currentTime = t; } });
  const claimsBy = {}; (o.claims || []).forEach((c) => { (claimsBy[c.group || "_"] ||= []).push(c); });
  const lines = o.script || [];
  const lastOfGroup = {}; lines.forEach((l, i) => { lastOfGroup[l.group] = i; });
  const items = lines.map((l, i) => h("li", { "data-t": l.t, onclick: () => { if (video.tagName === "VIDEO") { video.currentTime = l.t; video.play(); } } },
    h("span", { cls: "num" }, `${Math.floor(l.t / 60)}:${String(Math.floor(l.t % 60)).padStart(2, "0")}`), h("span", {}, capParts(l.text)),
    lastOfGroup[l.group] === i && claimsBy[l.group] ? h("div", { cls: "facts" }, [...new Map(claimsBy[l.group].map((c) => [c.source_url, c])).values()].map((c) => srcChip(c.source_url, c.claim))) : null));
  if (video.tagName === "VIDEO") video.addEventListener("timeupdate", () => {
    let k = -1; lines.forEach((l, i) => { if (video.currentTime >= l.t - 0.05) k = i; });
    items.forEach((li, i) => li.classList.toggle("on", i === k));
  });
  const qc = o.qc || {}; const busy = S.busy[j.id]; const openBox = !!S.changeOpen[j.id];
  const notes = h("textarea", { maxlength: "2000", placeholder: "What should change? For example: shorter hook, a different opening shot, say the number in English", "aria-label": "Changes for the next version",
    oninput: (e) => { S.drafts[j.id] = e.target.value; } });
  notes.value = S.drafts[j.id] || "";
  const changeBox = h("div", { style: openBox ? "" : "display:none" }, notes);
  const approve = h("button", { cls: "btn go", disabled: !!busy, onclick: () => act(j, "approve", () => rpc("set_review_decision", { p_job: j.id, p_decision: "approve", p_notes: null }), (row) => row?.output?.ig?.state ? "Approved. Posting to Instagram now; the link appears in Library." : ownPosting(j) ? "Approved. It's in the Library." : `Approved. It's in ${brandOf(j.brand).name}'s Library (nothing was posted).`) },
    icon("check"), busy === "approve" ? "Approving…" : autoOn(j) ? "Approve & post now" : "Approve");
  const change = h("button", { cls: "btn", disabled: !!busy, onclick: () => {
    if (!S.changeOpen[j.id]) { S.changeOpen[j.id] = true; if (deadline && !o.hold) rpc("hold_auto_post", { p_job: j.id, p_hold: true }).then(patchLocal).catch(() => {});   // writing changes pauses auto-post
      refreshProduction(); setTimeout(() => document.querySelector(`[data-notes="${j.id}"]`)?.focus(), 60); return; }
    const text = (S.drafts[j.id] || "").trim(); if (!text) { notes.focus(); toast("Write what should change first.", { err: true }); return; }
    act(j, "changes", async () => { const c = await rpc("request_changes", { p_job: j.id, p_notes: text }); delete S.drafts[j.id]; delete S.changeOpen[j.id];
      patchLocal({ id: j.id, status: "cancelled", output: { ...j.output, decision: "reject" } }); return c; }, "Sent back. The new version is queued; unchanged voice parts and clips are reused.");
  } }, busy === "changes" ? "Sending…" : openBox ? "Send changes" : "Request changes");
  notes.setAttribute("data-notes", j.id);
  const deadline = o.review_deadline && autoOn(j) && !o.autopilot_hold ? Date.parse(o.review_deadline) : 0;
  const timer = h("span", { cls: "num" });
  const tickT = () => { const s = Math.max(0, Math.round((deadline - Date.now()) / 1000)); timer.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  if (deadline && !o.hold) { tickT(); const iv = setInterval(() => { if (!timer.isConnected) return clearInterval(iv); tickT(); }, 1000); }
  const holdBtn = (hold, label) => h("button", { cls: "btn quiet", disabled: !!busy, onclick: () => act(j, "hold", () => rpc("hold_auto_post", { p_job: j.id, p_hold: hold }), hold ? "Auto-post paused. Approve when you're happy." : "Timer restarted.") }, label);
  const autoBox = o.autopilot_hold ? h("div", { cls: "banner warn autopost" }, h("span", {}, o.autopilot_note || "Autopilot held this reel back. It posts only when you approve."))
    : deadline && nightWait(j) ? h("div", { cls: "banner info autopost" }, h("span", {}, "Autopilot never posts at night: this reel goes out at 7:30 AM unless you approve it now."))
    : !deadline ? null : o.hold
    ? h("div", { cls: "banner info autopost" }, h("span", {}, "Auto-post is paused. Nothing goes to Instagram until you approve."), holdBtn(false, "Restart timer"))
    : h("div", { cls: "banner warn autopost" }, h("span", {}, "Posts to Instagram automatically in ", timer, " unless you approve, request changes or pause."), holdBtn(true, "Wait, I'm checking"));
  const cancelChange = openBox && !busy ? h("button", { cls: "btn quiet", onclick: () => { delete S.changeOpen[j.id]; refreshProduction(); } }, "Close") : null;
  return h("div", { cls: "rv" }, h("div", { cls: "phone" }, video),
    h("div", {},
      autoBox,
      h("div", { cls: "figures" }, h("div", {}, h("b", {}, `${o.duration ?? "–"} s`), "Length"), h("div", {}, h("b", {}, `${o.lufs ?? "–"}`), "Loudness (LUFS)"),
        h("div", {}, h("b", {}, `${qc.score ?? "–"}/10`), "Claude check"), h("div", {}, h("b", {}, `${o.delivery_mb ?? "–"} MB`), "File size")),
      (o.weak_words || []).length ? h("div", { cls: "banner warn", style: "margin-bottom:14px" }, `Listen closely to: ${o.weak_words.join(", ")}. The speech check was least sure about these.`) : null,
      lines.length ? [h("h3", { style: "margin-bottom:8px" }, "Script"), h("p", { cls: "small muted", style: "margin-bottom:10px" }, "Tap a line to jump the video there. Sources sit under the lines they support."), h("ol", { cls: "lines" }, items)] : null,
      h("div", { cls: "qc" }, h("h3", {}, "Claude's check"), h("p", { cls: "small", style: "margin-top:6px" }, qc.summary || "No notes."), (qc.issues || []).length ? h("ul", {}, qc.issues.map((i) => h("li", {}, `At ${i.t ?? "?"} s: ${i.problem}`))) : null),
      o.ig_caption ? h("details", { cls: "more" }, h("summary", {}, "Instagram caption"), h("p", { cls: "small", style: "white-space:pre-wrap;margin-top:8px" }, o.ig_caption)) : null,
      (o.credits || []).length ? h("details", { cls: "more" }, h("summary", {}, "Footage credits"), h("ul", {}, o.credits.map((c) => h("li", {}, `${c.author}, ${c.source} (${c.licence})`)))) : null,
      o.archive_on_mac ? h("p", { cls: "tiny faint", style: "margin-top:12px;overflow-wrap:anywhere" }, `Full-quality master on the Mac: ${o.archive_on_mac}`) : null,
      h("div", { cls: "decide" }, changeBox, h("div", { cls: "row" }, dl ? h("button", { cls: "btn quiet", onclick: () => download(o.video, name) }, icon("down"), "Download") : null, cancelChange, change, approve))));
}
let prodBox = null, prodBusy = false, prodAgain = false;
const stripCache = new Map();
function stripSig(j) {
  const now = Date.now();
  return JSON.stringify([j.status, j.stage, j.updated_at, j.error, j.not_before, stopping(j), S.open[j.id], S.sel[j.id], S.busy[j.id], (S.confirm[j.id] || 0) > now,
    S.changeOpen[j.id], macAlive(), ACTIVE.includes(j.status) ? [S.worker?.info?.blocked, S.worker?.info?.cap_hit, Math.floor(now / (j.status === "running" ? 10e3 : 30e3))] : 0]);
}
// Only strips whose data changed are rebuilt; a strip being typed in or playing video is never rebuilt under the user's finger.
async function refreshProduction() {
  if (!prodBox) return;
  if (prodBusy) { prodAgain = true; return; }
  prodBusy = true;
  try {
    const rank = { review: 0, running: 1, waiting_quota: 2, queued: 3, failed: 4, cancelled: 5 };
    const list = S.reels.filter(mine).sort((a, b) => rank[a.status] - rank[b.status] || new Date(a.created_at) - new Date(b.created_at));
    if (!list.length) {
      stripCache.clear();
      if (!prodBox.querySelector(".empty")) prodBox.replaceChildren(h("div", { cls: "panel empty" }, routeArt("chart"), h("h3", {}, "No reels in the air"), h("p", {}, "Start one from Create; it shows up here with a live route and ETA."), h("button", { cls: "btn primary", onclick: () => go("create") }, "Create a reel")));
      return;
    }
    const els = [];
    for (const j of list) {
      const c = stripCache.get(j.id), sig = stripSig(j);
      const inUse = c && c.status === j.status && ((c.el.contains(document.activeElement) && /^(TEXTAREA|INPUT)$/.test(document.activeElement.tagName)) || [...c.el.querySelectorAll("video")].some((v) => !v.paused));
      if (c && (c.sig === sig || inUse)) { els.push(c.el); continue; }
      let el; try { el = await strip(j); } catch { el = c?.el || h("article", { cls: "strip" }, h("div", { cls: "strip-head" }, h("h3", {}, titleOf(j)))); }
      stripCache.set(j.id, { sig, el, status: j.status }); els.push(el);
    }
    for (const id of [...stripCache.keys()]) if (!list.some((j) => j.id === id)) stripCache.delete(id);
    const kids = [...prodBox.children];
    if (kids.length !== els.length || els.some((e, i) => kids[i] !== e)) prodBox.replaceChildren(...els);
  } finally { prodBusy = false; if (prodAgain) { prodAgain = false; refreshProduction(); } }
}
function systemBanner() {
  const w = S.worker, i = w?.info || {};
  if (!S.net.ok) return h("div", { cls: "banner warn", style: "margin-bottom:14px" }, "Connection to the studio dropped. Showing the last known status; it updates by itself when the connection is back. Nothing on the Mac stops.");
  if (!macAlive()) return h("div", { cls: "banner err", style: "margin-bottom:14px" }, `The studio Mac is offline (last heard from ${w ? ago(w.last_seen) : "never"}). Reels wait and continue from where they stopped once it's back.`);
  if (i.blocked) return h("div", { cls: "banner warn", style: "margin-bottom:14px" }, `The Mac has ${i.blocked}. New reels start once space is freed.`);
  if (w && w.claude_ok === false) return h("div", { cls: "banner warn", style: "margin-bottom:14px" }, `Claude is not ready: ${w.claude_note || "unknown reason"}. Reels pause at the next Claude step and continue by themselves.`);
  if (i.cap_hit) return h("div", { cls: "banner info", style: "margin-bottom:14px" }, `Today's limit of ${i.daily_cap} new reels is reached. New reels start after midnight; requested changes still run today.`);
  return null;
}
function viewProduction() {
  prodBox = h("div", { cls: "strips" }, h("div", { cls: "skeleton" })); stripCache.clear();
  const banner = h("div"); const paintBanner = () => banner.replaceChildren(systemBanner() || "");
  shell(h("div", {}, h("div", { cls: "page-head" }, h("div", {}, h("h1", {}, "Production"), h("p", {}, "Every reel in progress, with its route, live log and ETA. Tap a stage to see each step."))), banner, prodBox));
  paintBanner(); S.timers.push(setInterval(paintBanner, 6000));
  refreshProduction();
  S.timers.push(setInterval(() => refreshProduction(), 5000));
}

// ---------------------------------------------------------------- library + Instagram auto-posting
async function loadSettings() {
  const { data, error } = await sb.from("settings").select("key,value"); if (error) throw error;
  S.settings = Object.fromEntries((data || []).map((r) => [r.key, r.value])); return S.settings;
}
const tzClock = (iso) => new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
const slotLabel = (hm) => { const [hh, mm] = hm.split(":").map(Number); return new Date(2000, 0, 1, hh, mm).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); };
const PLAT = { ig: "Instagram", li: "LinkedIn", fb: "Facebook" };
function postBadge(j, p) {
  const x = j.output?.[p] || {}; const st = x.state || "off"; const name = PLAT[p];
  const row = (cls, text, extra) => h("div", { cls: "ig-state " + cls }, h("i", { cls: "lamp " + (cls === "ok" ? "ok" : cls === "bad" ? "bad" : cls === "wait" ? "wait" : "") }), h("span", {}, text), extra || null);
  if (st === "posted") return row("ok", `On ${name}`, safeUrl(x.permalink) ? h("a", { href: x.permalink, target: "_blank", rel: "noopener noreferrer" }, "View post") : null);
  if (st === "posting") return row("wait", `Posting to ${name} now…`);
  if (st === "uploading") return row("wait", `Uploading to ${name}…`);
  if (st === "processing") return row("wait", `${name} is processing the video…`);
  if (st === "scheduled") { const soon = Date.parse(x.at) <= Date.now() + 60e3;
    return [row(soon ? "wait" : "", soon ? `Posting to ${name} in about a minute` : `${name}: next try ${tzClock(x.at)}`), x.error ? h("p", { cls: "ig-note" }, x.error) : null]; }
  if (st === "queued") return row("wait", `Posting to ${name} shortly`);
  if (st === "failed") return [row("bad", `${name}: posting failed`), h("p", { cls: "ig-note" }, (x.error || "").replace(/^Posting failed:\s*/, ""))];
  return row("", `Not posted to ${name}`);
}
function postActions(j, p, refresh) {
  const st = j.output?.[p]?.state || "off";
  if (["posted", "posting", "uploading", "processing"].includes(st)) return null;
  if (!ownPosting(j)) return h("p", { cls: "ig-note" }, `Connect ${brandOf(j.brand).name}'s own ${PLAT[p]} to post. Reels never go to another account.`);
  const go_ = (action, label, cls = "btn quiet") => h("button", { cls, onclick: async (e) => {
    e.currentTarget.disabled = true;
    try { const row = await rpc("set_post", { p_job: j.id, p_platform: p, p_action: action }); j.output = row.output;
      toast(action === "now" ? `Posting to ${PLAT[p]} within about 2 minutes.` : `Won't be posted to ${PLAT[p]}.`); }
    catch (err) { toast(errText(err), { err: true }); }
    refresh();
  } }, label);
  return h("div", { cls: "ig-acts" }, st === "failed" ? go_("now", `Retry ${PLAT[p]}`, "btn primary") : go_("now", `Post to ${PLAT[p]}`),
    ["scheduled", "queued"].includes(st) ? go_("off", "Don't post") : null);
}
const liSetUp = () => (S.settings?.linkedin_status?.state || "not_connected") !== "not_connected";
const LI_SCOPES = "w_organization_social r_organization_social rw_organization_admin";
const LI_REDIRECT = location.origin + "/";
function liSignIn(clientId) {
  const state = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
  try { sessionStorage.setItem("li_state", state); } catch { /* */ }
  location.href = "https://www.linkedin.com/oauth/v2/authorization?" + new URLSearchParams({ response_type: "code", client_id: clientId, redirect_uri: LI_REDIRECT, state, scope: LI_SCOPES });
}
function liPanel(refresh) {
  const st = S.settings?.linkedin_status || { state: "not_connected" }; const app = S.settings?.linkedin_app || {};
  const days = st.expires_at ? Math.floor((Date.parse(st.expires_at) - Date.now()) / 86400e3) : null;
  const lamp = st.state === "connected" ? (days != null && days < 7 ? "wait" : "ok") : st.state === "not_connected" ? "" : "bad";
  const text = st.state === "connected" ? `Posting to ${st.org_name} on LinkedIn` : st.state === "not_connected" ? "LinkedIn Company Page is not connected yet" : "LinkedIn sign-in expired";
  return h("section", { cls: "panel igp", "aria-label": "LinkedIn" },
    h("div", { cls: "igp-head" }, h("div", {}, h("h3", {}, h("i", { cls: "lamp " + lamp }), text),
      st.note ? h("p", { cls: "small", style: "margin-top:4px;color:#7D1C26" }, st.note) : null,
      st.state === "connected" && days != null ? h("p", { cls: "tiny " + (days < 7 ? "" : "faint"), style: "margin-top:2px" + (days < 7 ? ";color:#6E4300;font-weight:600" : "") },
        `LinkedIn asks for a fresh sign-in every 60 days: reconnect by ${dateShort(st.expires_at)}${days < 7 ? " (soon)" : ""}.`) : null),
      h("div", { cls: "igp-btns" },
        app.client_id && st.state !== "not_connected" ? h("button", { cls: st.state === "connected" && (days ?? 99) >= 7 ? "btn quiet" : "btn primary", onclick: () => liSignIn(app.client_id) }, "Reconnect LinkedIn") : null,
        h("button", { cls: st.state === "not_connected" ? "btn primary" : "btn quiet", onclick: () => liDialog(refresh) }, st.state === "not_connected" ? "Connect LinkedIn" : "App settings"),
        st.state !== "not_connected" ? h("button", { cls: "btn quiet danger", onclick: async (e) => { if (!e.currentTarget._armed) { e.currentTarget._armed = true; e.currentTarget.textContent = "Tap again to disconnect"; return; }
          try { await rpc("disconnect_linkedin", {}); await loadSettings(); toast("LinkedIn disconnected. Nothing will be posted there."); } catch (err) { toast(errText(err), { err: true }); } refresh(); } }, "Disconnect") : null)),
    st.state === "connected" ? h("p", { cls: "small muted", style: "margin-top:6px" }, "Every reel that goes to Instagram is also posted to the Company Page, with the same caption.") : null);
}
function liDialog(refresh) {
  const app = S.settings?.linkedin_app || {};
  const cid = h("input", { type: "text", autocomplete: "off", spellcheck: "false", placeholder: "Client ID", value: app.client_id || "", "aria-label": "LinkedIn Client ID" });
  const sec = h("input", { type: "password", autocomplete: "off", spellcheck: "false", placeholder: app.client_id ? "Client Secret (leave empty to keep the saved one)" : "Client Secret", "aria-label": "LinkedIn Client Secret", style: "margin-top:8px" });
  const msg = h("p", { cls: "small", role: "status", style: "min-height:20px;margin-top:8px" });
  const go_ = h("button", { cls: "btn primary", onclick: async () => {
    if (!cid.value.trim()) { cid.focus(); return; }
    go_.disabled = true; msg.textContent = "Saving…";
    try {
      if (sec.value.trim() || !app.client_id || cid.value.trim() !== app.client_id) {
        if (!sec.value.trim()) { msg.textContent = "Paste the Client Secret too."; go_.disabled = false; sec.focus(); return; }
        await rpc("set_linkedin_app", { p_client_id: cid.value.trim(), p_client_secret: sec.value.trim() }); sec.value = "";
      }
      msg.textContent = "Opening LinkedIn…"; liSignIn(cid.value.trim());
    } catch (e) { msg.textContent = errText(e); go_.disabled = false; }
  } }, "Save and sign in with LinkedIn");
  const steps = [
    ["Create a new app", "linkedin.com/developers/apps → Create app → LinkedIn Page: We One Aviation. It must be a new app: LinkedIn only gives page posting to an app with no other products (your current one has Share on LinkedIn)."],
    ["Request page posting", "Products → Community Management API → Request access. Use your business email, the company's legal name and this privacy policy: " + location.origin + "/privacy. LinkedIn reviews it and emails you."],
    ["Add the return address", "Auth tab → Authorized redirect URLs → add " + LI_REDIRECT],
    ["Connect", "After approval, paste the new app's Client ID and Client Secret here and sign in as an admin of the We One Aviation page. The secret stays on the server."]];
  const d = h("dialog", { "aria-label": "Connect LinkedIn", cls: "connect" }, h("div", { style: "padding:20px 20px 4px" },
    h("h2", { style: "font-size:22px" }, "Connect the LinkedIn Company Page"),
    h("p", { cls: "small muted", style: "margin:6px 0 12px" }, "Free, official LinkedIn API. Reels then post to the page as We One Aviation."),
    h("ol", { cls: "steps" }, steps.map(([t, x]) => h("li", {}, h("b", {}, t), h("span", {}, x)))), cid, sec, msg),
    h("div", { cls: "dlg-foot" }, h("button", { cls: "btn quiet", onclick: () => d.close() }, "Close"), go_));
  d.addEventListener("close", () => d.remove()); document.body.append(d); d.showModal(); (app.client_id ? sec : cid).focus();
}
const fbSetUp = () => (S.settings?.facebook_status?.state || "not_connected") !== "not_connected";
function fbPanel(refresh) {
  const st = S.settings?.facebook_status || { state: "not_connected" };
  const lamp = st.state === "connected" ? "ok" : st.state === "not_connected" ? "" : "bad";
  const text = st.state === "connected" ? `Posting to ${st.page_name} on Facebook` : st.state === "not_connected" ? "Facebook Page is not connected yet" : "Facebook connection stopped working";
  return h("section", { cls: "panel igp", "aria-label": "Facebook" },
    h("div", { cls: "igp-head" }, h("div", {}, h("h3", {}, h("i", { cls: "lamp " + lamp }), text),
      st.note && st.state !== "connected" ? h("p", { cls: "small", style: "margin-top:4px;color:#7D1C26" }, st.note) : null,
      st.state === "connected" ? h("p", { cls: "tiny faint", style: "margin-top:2px" }, "Page connections do not expire; reconnect only if you change your Facebook password or remove access.") : null),
      h("div", { cls: "igp-btns" },
        h("button", { cls: st.state === "connected" ? "btn quiet" : "btn primary", onclick: () => fbDialog(refresh) }, st.state === "not_connected" ? "Connect Facebook" : "Reconnect"),
        st.state !== "not_connected" ? h("button", { cls: "btn quiet danger", onclick: async (e) => { if (!e.currentTarget._armed) { e.currentTarget._armed = true; e.currentTarget.textContent = "Tap again to disconnect"; return; }
          try { await rpc("disconnect_facebook", {}); await loadSettings(); toast("Facebook disconnected. Nothing will be posted there."); } catch (err) { toast(errText(err), { err: true }); } refresh(); } }, "Disconnect") : null)));
}
function fbDialog(refresh) {
  const inp = (ph, type = "text") => h("input", { type, autocomplete: "off", spellcheck: "false", placeholder: ph, "aria-label": ph, style: "margin-top:8px" });
  const appId = inp("App ID (numbers)"), secret = inp("App Secret", "password"), tok = inp("Access token from Graph API Explorer", "password");
  const msg = h("p", { cls: "small", role: "status", style: "min-height:20px;margin-top:8px" });
  const go_ = h("button", { cls: "btn primary", onclick: async () => {
    if (!appId.value.trim() || !secret.value.trim() || !tok.value.trim()) { msg.textContent = "Fill in all three boxes."; return; }
    go_.disabled = true; msg.textContent = "Connecting…";
    const { data, error } = await sb.functions.invoke("fb-auth", { body: { app_id: appId.value.trim(), app_secret: secret.value.trim(), user_token: tok.value.trim() } });
    let err = data?.error; if (error && !err) { try { err = (await error.context?.json())?.error; } catch { /* */ } err ||= error.message; }
    if (err) { msg.textContent = err; go_.disabled = false; return; }
    secret.value = ""; tok.value = ""; toast(`Facebook connected: reels now post to ${data.page_name} too.`); await loadSettings().catch(() => {}); d.close(); refresh();
  } }, "Connect");
  const steps = [
    ["Add the Page use case", "developers.facebook.com → your Reel Studio app → Use cases → Add use case → Manage everything on your Page."],
    ["Copy App ID and App Secret", "App settings → Basic. Use this App ID (not the Instagram app ID); click Show for the secret."],
    ["Get a token", "Tools → Graph API Explorer → Meta App: Reel Studio → Get User Access Token → tick pages_show_list, pages_read_engagement, pages_manage_posts → Generate → choose the We One Aviation page → copy the token."],
    ["Connect", "Paste the three values below. Only the Page's own token is kept, on the server; it does not expire."]];
  const d = h("dialog", { "aria-label": "Connect Facebook", cls: "connect" }, h("div", { style: "padding:20px 20px 4px" },
    h("h2", { style: "font-size:22px" }, "Connect the Facebook Page"),
    h("p", { cls: "small muted", style: "margin:6px 0 12px" }, "Free, official Facebook API. Reels post to the Page as We One Aviation."),
    h("ol", { cls: "steps" }, steps.map(([t, x]) => h("li", {}, h("b", {}, t), h("span", {}, x)))), appId, secret, tok, msg),
    h("div", { cls: "dlg-foot" }, h("button", { cls: "btn quiet", onclick: () => d.close() }, "Close"), go_));
  d.addEventListener("close", () => d.remove()); document.body.append(d); d.showModal(); appId.focus();
}
async function finishLinkedIn() {     // back from LinkedIn's sign-in page: ?code=…&state=…
  const q = new URLSearchParams(location.search); if (!q.has("code") && !q.has("error")) return;
  history.replaceState(null, "", location.pathname + "#/library");
  let expected = null; try { expected = sessionStorage.getItem("li_state"); sessionStorage.removeItem("li_state"); } catch { /* */ }
  if (q.has("error")) { toast(`LinkedIn: ${q.get("error_description") || q.get("error")}`, { err: true, ms: 9000 }); return; }
  if (!expected || q.get("state") !== expected) { toast("LinkedIn sign-in could not be verified. Start Connect LinkedIn again.", { err: true, ms: 9000 }); return; }
  toast("Connecting the LinkedIn page…", { ms: 4000 });
  const { data, error } = await sb.functions.invoke("linkedin-auth", { body: { code: q.get("code"), redirect_uri: LI_REDIRECT } });
  let msg = data?.error; if (error && !msg) { try { msg = (await error.context?.json())?.error; } catch { /* */ } msg ||= error.message; }
  if (msg) toast(`LinkedIn: ${msg}`, { err: true, ms: 12000 });
  else toast(`LinkedIn connected: reels now post to ${data.org_name} too.`, { ms: 8000 });
  await loadSettings().catch(() => {}); go("library");
}
// ---------------------------------------------------------------- Library: one small tile per platform (tap = the same pop-up as before)
function platformBar(refresh) {
  const items = [["ig", "Instagram", S.settings?.instagram_status, igPanel, connectDialog],
                 ["fb", "Facebook", S.settings?.facebook_status, fbPanel, fbDialog],
                 ["li", "LinkedIn", S.settings?.linkedin_status, liPanel, liDialog]];
  return h("section", { cls: "plats", "aria-label": "Connected accounts" }, items.map(([k, name, st, panelFn, connectFn]) => {
    const state = st?.state || "not_connected";
    const kind = state === "connected" ? "ok" : state === "not_connected" ? "no" : state === "checking" ? "wait" : "warn";
    const word = { ok: "Connected", no: "Not connected", wait: "Checking…", warn: "Needs attention" }[kind];
    const who = state === "connected" ? (k === "ig" ? `@${st.username}` : k === "fb" ? st.page_name : st.org_name) : "";
    return h("button", { cls: `plat ${k}`, "aria-label": `${name}: ${word}${who ? ", " + who : ""}. Tap for options.`, title: who || word,
        onclick: () => (state === "not_connected" ? connectFn(refresh) : platformDialog(name, panelFn, refresh)) },
      h("span", { cls: "plat-tile" }, h("img", { src: PLAT_IMG[k], alt: "", width: 56, height: 56 }),
        h("span", { cls: `plat-badge ${kind}`, "aria-hidden": "true" }, kind === "ok" ? "✓" : kind === "no" ? "✕" : kind === "wait" ? "…" : "!")),
      h("span", { cls: "plat-name" }, name), h("span", { cls: `plat-state ${kind}` }, word));
  }));
}
function platformDialog(name, panelFn, refresh) {
  const inner = h("div");
  const rr = () => { refresh(); if (inner.isConnected) inner.replaceChildren(panelFn(rr)); };
  inner.append(panelFn(rr));
  const d = h("dialog", { "aria-label": name, cls: "connect plat-dlg" }, h("div", { style: "padding:16px 16px 0" }, inner),
    h("div", { cls: "dlg-foot" }, h("span"), h("button", { cls: "btn quiet", onclick: () => d.close() }, "Close")));
  d.addEventListener("close", () => d.remove()); document.body.append(d); d.showModal();
}
function igPanel(refresh) {
  const st = S.settings?.instagram_status || { state: "not_connected" }; const sch = S.settings?.post_schedule || { slots: ["19:30"], auto: true };
  const lamp = st.state === "connected" ? "ok" : st.state === "checking" ? "wait" : st.state === "not_connected" ? "" : "bad";
  const text = { connected: `Connected as @${st.username}`, checking: "Checking the connection with Instagram…", not_connected: "Instagram is not connected yet",
    expired: "Instagram connection expired", error: "Instagram is not reachable right now" }[st.state] || st.state;
  const editing = !!S.editSlots;
  const mins = sch.review_minutes ?? 5;
  const slotsView = editing ? slotEditor(sch, refresh) : h("p", { cls: "small muted", style: "margin-top:6px" },
    sch.auto === false ? "Auto-posting is off: approved reels post only when you tap Post now." :
      `Approve & post now, or do nothing: ${mins} minutes after a reel is ready it posts by itself to Instagram and every connected Page. Request changes or "Wait, I'm checking" stops it. Posting runs in the cloud, so the Mac can be off.`,
    " ", h("button", { cls: "linkish", onclick: () => { S.editSlots = { minutes: mins, auto: sch.auto !== false }; refresh(); } }, "Change"));
  return h("section", { cls: "panel igp", "aria-label": "Instagram" },
    h("div", { cls: "igp-head" }, h("div", {}, h("h3", {}, h("i", { cls: "lamp " + lamp }), text),
      st.note && st.state !== "connected" ? h("p", { cls: "small", style: "margin-top:4px;color:#7D1C26" }, st.note) : null,
      st.state === "connected" && st.expires_at ? h("p", { cls: "tiny faint", style: "margin-top:2px" }, "The connection renews itself automatically.") : null),
      h("div", { cls: "igp-btns" }, h("button", { cls: st.state === "connected" ? "btn quiet" : "btn primary", onclick: () => connectDialog(refresh) }, st.state === "connected" ? "Reconnect" : "Connect Instagram"),
        st.state !== "not_connected" ? h("button", { cls: "btn quiet danger", onclick: async (e) => { if (!e.currentTarget._armed) { e.currentTarget._armed = true; e.currentTarget.textContent = "Tap again to disconnect"; return; }
          try { await rpc("disconnect_instagram", {}); await loadSettings(); toast("Instagram disconnected. Nothing will be posted."); } catch (err) { toast(errText(err), { err: true }); } refresh(); } }, "Disconnect") : null)),
    slotsView);
}
function slotEditor(sch, refresh) {
  const ed = S.editSlots;
  const minutes = h("input", { type: "number", min: "1", max: "60", value: String(ed.minutes), style: "width:90px", "aria-label": "Minutes to wait for approval", oninput: (e) => { ed.minutes = Number(e.target.value); } });
  const auto = h("input", { type: "checkbox", id: "autopost", onchange: (e) => { ed.auto = e.target.checked; } }); auto.checked = ed.auto;
  return h("div", { cls: "slot-edit" },
    h("label", { cls: "small", for: "autopost", style: "display:flex;gap:8px;align-items:center" }, auto, "Post to Instagram automatically if I don't respond"),
    h("label", { cls: "small", style: "display:flex;gap:8px;align-items:center;margin-top:10px" }, "Wait for my approval", minutes, "minutes"),
    h("div", { style: "display:flex;gap:8px;margin-top:12px" },
      h("button", { cls: "btn primary", onclick: async (e) => { e.currentTarget.disabled = true;
        try { await rpc("set_autopost", { p_auto: ed.auto, p_minutes: Math.round(ed.minutes) }); S.editSlots = null; await loadSettings(); toast("Saved."); }
        catch (err) { toast(errText(err), { err: true }); } refresh(); } }, "Save"),
      h("button", { cls: "btn quiet", onclick: () => { S.editSlots = null; refresh(); } }, "Cancel")));
}
function connectDialog(refresh) {
  const tok = h("input", { type: "password", autocomplete: "off", spellcheck: "false", placeholder: "Paste the access token", "aria-label": "Instagram access token" });
  const msg = h("p", { cls: "small", role: "status", style: "min-height:20px;margin-top:8px" });
  const save = h("button", { cls: "btn primary", onclick: async () => {
    if (!tok.value.trim()) { tok.focus(); return; }
    save.disabled = true; msg.textContent = "Saving…";
    try {
      await rpc("set_instagram_token", { p_token: tok.value.trim() }); tok.value = ""; msg.textContent = "Saved. The Mac is checking it with Instagram…";
      for (let i = 0; i < 30; i++) { await sleep(3000); await loadSettings(); const st = S.settings.instagram_status || {};
        if (st.state === "connected") { msg.textContent = `Connected as @${st.username}.`; toast(`Instagram connected as @${st.username}.`); await sleep(900); d.close(); refresh(); return; }
        if (st.state === "expired" || st.state === "error") { msg.textContent = st.note || "Instagram did not accept the token."; save.disabled = false; return; } }
      msg.textContent = macAlive() ? "Still checking; this page updates when the Mac finishes." : "Saved. The Mac checks it as soon as it is back online."; save.disabled = false;
    } catch (e) { msg.textContent = errText(e); save.disabled = false; }
  } }, "Save and check");
  const steps = [
    ["Open developers.facebook.com/apps", "Create App → use case Other → type Business → name it (e.g. Reel Studio)."],
    ["Add Instagram", "Instagram → Set up → API setup with Instagram login."],
    ["Add your account", "Generate access tokens → Add account → log in to the Instagram account the reels are for."],
    ["Copy the token", "Click Generate token next to the account, copy it, paste it below. It stays on the server; the site never shows it again."]];
  const d = h("dialog", { "aria-label": "Connect Instagram", cls: "connect" }, h("div", { style: "padding:20px 20px 4px" },
    h("h2", { style: "font-size:22px" }, "Connect Instagram"),
    h("p", { cls: "small muted", style: "margin:6px 0 12px" }, "Free, official Instagram API. One time, about 5 minutes."),
    h("ol", { cls: "steps" }, steps.map(([t, x]) => h("li", {}, h("b", {}, t), h("span", {}, x)))), tok, msg),
    h("div", { cls: "dlg-foot" }, h("button", { cls: "btn quiet", onclick: () => d.close() }, "Close"), save));
  d.addEventListener("close", () => d.remove()); document.body.append(d); d.showModal(); tok.focus();
}
async function viewLibrary() {
  const panel = h("div"); const grid = h("div", { cls: "lib" }, [1, 2, 3, 4].map(() => h("div", { cls: "skeleton", style: "aspect-ratio:9/16;height:auto" })));
  shell(h("div", {}, h("div", { cls: "page-head" }, h("div", {}, h("h1", {}, "Library"), h("p", {}, "Approved reels and their Instagram, Facebook and LinkedIn posts. Cloud copies are kept for 30 days; the full-quality masters stay on the Mac."))), panel, grid));
  const thumbs = {}; let sig = "";
  const refresh = async (force = true) => {
    if (!grid.isConnected) return;
    const [{ data }] = await Promise.all([sb.from("jobs").select("id,input,output,updated_at").eq("kind", "reel").eq("status", "done").eq("brand", S.brand).order("updated_at", { ascending: false }).limit(60), loadSettings().catch(() => {})]);
    const now = JSON.stringify([(data || []).map((j) => [j.id, j.output?.ig, j.output?.li, j.output?.fb]), S.settings, S.editSlots]);
    if (!force && now === sig) return; sig = now;
    if (panel.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;    // never wipe a half-edited time
    panel.replaceChildren(!isLive() ? setupCard() : ownPosting() ? platformBar(() => refresh(true))
      : h("div", { cls: "banner info" }, h("span", {}, `${brandOf().name} has no Instagram, Facebook or LinkedIn connected yet. Approved reels are kept here to download; nothing is posted to another account.`)));
    if (!data?.length) { grid.replaceChildren(h("div", { cls: "panel empty", style: "grid-column:1/-1" }, h("h3", {}, "Nothing here yet"), h("p", {}, "Approved reels appear here, ready to download and post."), h("button", { cls: "btn primary", onclick: () => go("create") }, "Create a reel"))); return; }
    const cards = await Promise.all(data.map(async (j) => {
      const o = j.output || {}; if (!(j.id in thumbs)) thumbs[j.id] = o.pruned ? null : await signed(o.thumb);
      return h("div", { cls: "libcard" }, h("button", { cls: "poster", onclick: () => openPlayer(j) }, h("div", { cls: "frame" }, thumbs[j.id] ? h("img", { src: thumbs[j.id], alt: "", loading: "lazy" }) : null, h("span", { cls: "play" }, icon("play", 18, true))),
        h("h3", {}, titleOf(j)), h("p", { cls: "tiny" }, `${dateShort(j.updated_at)}, ${o.duration ?? "?"} s`)), postBadge(j, "ig"), postActions(j, "ig", () => refresh(true)),
        fbSetUp() || o.fb ? [postBadge(j, "fb"), postActions(j, "fb", () => refresh(true))] : null,
        liSetUp() || o.li ? [postBadge(j, "li"), postActions(j, "li", () => refresh(true))] : null);
    }));
    grid.replaceChildren(...cards);
  };
  await refresh(true);
  S.timers.push(setInterval(() => refresh(false).catch(() => {}), 15000));
}
async function openPlayer(j) {
  const o = j.output || {}; const name = `${titleOf(j).replace(/[^\w\- ]+/g, "").trim().slice(0, 50) || "reel"}.mp4`;
  const [url, dl] = o.pruned ? [null, null] : await Promise.all([signed(o.video), signed(o.video, name)]);
  const d = h("dialog", { "aria-label": titleOf(j) }, url ? h("video", { src: url, controls: true, autoplay: true, playsinline: true }) : h("p", { style: "padding:24px" }, "The cloud copy was removed after 30 days. The master is on the Mac."),
    h("div", { cls: "dlg-foot" }, h("b", { style: "font-family:var(--f-head)" }, titleOf(j)), h("div", { style: "display:flex;gap:8px" },
      dl ? h("button", { cls: "btn primary", onclick: () => download(o.video, name) }, icon("down"), "Download") : null, h("button", { cls: "btn", onclick: () => d.close() }, "Close"))));
  d.addEventListener("close", () => d.remove()); document.body.append(d); d.showModal();
}

// ---------------------------------------------------------------- background polling: badge, status lamps, ready alerts
let polling = null;
function poll() { return polling ||= pollOnce().finally(() => { polling = null; }); }      // overlapping calls share one round-trip
async function pollOnce() {
  if (!S.session) return;
  try {
    await Promise.all([loadReels(), loadWorker(), loadSettings().catch(() => {})]);
    if (!S.net.ok) S.net = { ok: true, since: 0 };
    const review = S.reels.filter((j) => j.status === "review").map((j) => j.id);
    if (S.seenReview === null) S.seenReview = new Set(review);
    else for (const id of review) if (!S.seenReview.has(id)) { S.seenReview.add(id); notifyReady(S.reels.find((j) => j.id === id)); }
    const failed = S.reels.filter((j) => j.status === "failed").map((j) => j.id);
    if (S.seenFailed === undefined) S.seenFailed = new Set(failed);
    else for (const id of failed) if (!S.seenFailed.has(id)) { S.seenFailed.add(id); const j = S.reels.find((x) => x.id === id);
      toast(`Stopped: ${titleOf(j)}. Saved progress is kept.`, { err: true, ms: 9000, action: { label: "Open", fn: () => go("production", id) } }); }
    const top = document.querySelector(".top"); if (top && S.route) { // refresh lamps + badge without touching page content
      const lamps = top.querySelectorAll(".status .lamp");
      if (lamps.length === 2) { lamps[0].className = "lamp " + (macAlive() ? "ok" : "bad"); lamps[1].className = "lamp " + (!macAlive() ? "" : S.worker?.claude_ok ? "ok" : "bad"); }
      const n = S.reels.filter((j) => j.status === "review").length || S.reels.filter((j) => ACTIVE.includes(j.status)).length;
      document.querySelectorAll('[aria-current] .badge, nav button .badge').forEach((b) => b.remove());
      if (n) document.querySelectorAll("nav button").forEach((b) => { if (b.textContent.trim().startsWith("Production")) b.append(h("span", { cls: "badge" }, n)); });
    }
    updateTitle();
  } catch (e) {
    if (isAuthErr(e)) {        // expired sign-in: refresh it once, otherwise send the user to the sign-in page (nothing is lost on the Mac)
      S.authFails = (S.authFails || 0) + 1;
      const { data } = await sb.auth.getSession().catch(() => ({ data: {} }));
      if (data?.session) { if (S.authFails >= 3) { const r = await sb.auth.refreshSession().catch(() => ({})); if (r?.data?.session) S.authFails = 0; } return; }
      if (navigator.onLine === false) return;                       // offline: keep the session, try again later
      toast("Your sign-in expired. Please sign in again.", { err: true }); await sb.auth.signOut({ scope: "local" });
      return;
    }
    if (S.net.ok) S.net = { ok: false, since: Date.now() };     // keep showing the last good data, with a banner
  }
}

// ---------------------------------------------------------------- boot
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js", { updateViaCache: "none" }).catch(() => {});
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); S.installEvt = e; });
try { S.checkedAt = Number(localStorage.getItem("rs_checked") || sessionStorage.getItem("rs_checked") || 0); if (Date.now() - S.checkedAt > 12 * 3600e3) S.checkedAt = 0; } catch { /* */ }
let booted = false;
sb.auth.onAuthStateChange(async (evt, session) => {
  const was = !!S.session; S.session = session;
  if (!booted) return;
  if (!session) { S.checkedAt = 0; try { sessionStorage.removeItem("rs_checked"); localStorage.removeItem("rs_checked"); } catch { /* */ } render(); return; }
  if (evt === "TOKEN_REFRESHED") S.authFails = 0;
  if (!was) { await Promise.all([loadEstimates(), poll(), findPending().catch(() => {}), loadSettings().catch(() => {}), loadBrands().catch(() => {})]); if (location.hash !== "#/home") { history.replaceState(null, "", "#/home"); } render(); }     // a fresh sign-in always lands on the Dashboard
});
const { data: { session } } = await sb.auth.getSession();
S.session = session;
async function findPending() {     // a reload or a second device re-attaches to idea requests already running
  const since = new Date(Date.now() - 20 * 60e3).toISOString();
  const { data } = await sb.from("jobs").select("id,kind").in("kind", ["suggest", "optimize"]).in("status", ACTIVE).gte("created_at", since).order("created_at", { ascending: false });
  for (const j of data || []) if (!S.pending[j.kind]) S.pending[j.kind] = j.id;
  for (const k of ["suggest", "optimize"]) if (S.pending[k] && !(data || []).some((j) => j.id === S.pending[k])) {
    const { data: d } = await sb.from("jobs").select("status").eq("id", S.pending[k]).maybeSingle(); if (!d) delete S.pending[k];
  }
  savePending();
}
if (session) { await loadEstimates(); await Promise.all([poll(), findPending().catch(() => {}), loadSettings().catch(() => {}), loadBrands().catch(() => {})]); }
booted = true; render();
if (session) finishLinkedIn().catch((e) => toast(errText(e), { err: true }));
setInterval(poll, 6000);
const wake = async () => { if (document.hidden || !S.session) return; await poll(); if (S.route === "production") refreshProduction(); };
document.addEventListener("visibilitychange", wake);       // phones freeze background tabs: catch up the moment the page is visible again
window.addEventListener("online", wake);
window.addEventListener("offline", () => { S.net = { ok: false, since: Date.now() }; });
setInterval(() => { if (S.session) loadEstimates(); }, 10 * 60e3);
