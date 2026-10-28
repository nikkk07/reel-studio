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
  home: "M4 11.5 12 4l8 7.5M6.5 9.5V20h11V9.5M10 20v-5h4v5", chart: "M5 20V11M11 20V5M17 20v-8M3 20h18", ext: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  plane: "M21 11.2 13.6 10 9.8 3H8.2l1.6 7.1-4.7.4-1.7-2.3H2.2l1 3.8-1 3.8h1.2l1.7-2.3 4.7.4L8.2 21h1.6l3.8-7 7.4-1.2c.6-.1 1-.4 1-.8s-.4-.7-1-.8Z",
  create: "M12 4v16M4 12h16", route: "M5 18c3-9 11-3 14-12M5 18a2 2 0 1 0 0 .1M19 6a2 2 0 1 0 0 .1",
  library: "M4 4h6v7H4zM14 4h6v4h-6zM14 12h6v8h-6zM4 15h6v5H4z", agents: "M12 3v3M8.5 8h7a3 3 0 0 1 3 3v5a3 3 0 0 1-3 3h-7a3 3 0 0 1-3-3v-5a3 3 0 0 1 3-3zM9.5 13h.01M14.5 13h.01M3 13h2.5M18.5 13H21", check: "M5 12.5l4.5 4.5L19 7.5", x: "M6 6l12 12M18 6 6 18",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3M8 21h8",
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
// each account's own connection status: We One keeps the original keys, other accounts use "<key>@<account>"
const bkey = (base, b = S.brand) => (!b || b === "weone" ? base : `${base}@${b}`);
const stOf = (base, b = S.brand) => S.settings?.[bkey(base, b)] || { state: "not_connected" };
const PST = { ig: "instagram_status", fb: "facebook_status", li: "linkedin_status" };
const platState = (k, b = S.brand) => stOf(PST[k], b).state || "not_connected";
const platSet = (k, b = S.brand) => !["not_connected", "duplicate"].includes(platState(k, b));
const anyPosting = (b = S.brand) => b === "weone" || ["ig", "fb", "li"].some((k) => platSet(k, b));
const SVC_IMG = { supabase: "icons/services/supabase.png", vercel: "icons/services/vercel.svg", github: "icons/services/github.svg", apple: "icons/services/apple.png",
  claude: "icons/services/claude.svg", meta: "icons/services/meta.svg", cloudflare: "icons/services/cloudflare.png", pexels: "icons/services/pexels.png", pixabay: "icons/services/pixabay.png" };
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
// a reel's video: Cloudflare R2 (a 7-day link saved with the reel) or Supabase (signed here); gone once retention removed it
async function videoUrl(o, name) {
  if (!o || o.pruned || !o.video) return null;
  if (o.store === "r2") return o.play_url && Date.parse(o.play_until || 0) > Date.now() ? o.play_url : null;
  return signed(o.video, name);
}
async function downloadReel(o, name) {
  if (o?.store === "r2") { const u = await videoUrl(o); if (!u) { toast("This video is no longer stored.", { err: true }); return; } window.open(u, "_blank", "noopener"); return; }
  return download(o.video, name);
}
const keptNote = (o) => o?.pruned ? `Video removed ${o.pruned_at ? ago(o.pruned_at) : ""} (${o.prune_reason || "storage limit"}). Results, script and thumbnail are kept.` : null;
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
  const studioLevel = STUDIO_ROUTES.includes(S.route) || !S.session;
  const items = studioLevel ? [["home", "Dashboard", "home"], ["settings", "Settings", "gear"]]
    : [["create", "Create", "create"], ["production", "Production", "route"], ["library", "Library", "library"], ["insights", "Insights", "chart"], ["agents", "Agents", "agents"], ["account", "Settings", "gear"]];
  const navBtn = ([k, label, ic], mobile) => h("button", { "aria-current": S.route === k ? "page" : null, onclick: () => go(k) },
    mobile ? icon(ic, 22) : null, label, k === "production" && badgeN ? h("span", { cls: "badge", "aria-label": `${badgeN} reels` }, badgeN) : null);
  const lamps = S.session ? h("button", { cls: "status", onclick: () => go("check"), title: "Run the pre-flight check" },
    h("span", {}, h("i", { cls: "lamp " + (macAlive() ? "ok" : "bad") }), h("span", { cls: "label" }, "Mac")),
    h("span", {}, h("i", { cls: "lamp " + (!macAlive() ? "" : S.worker?.claude_ok ? "ok" : "bad") }), h("span", { cls: "label" }, "Claude"))) : null;
  const initials = (S.session?.user?.email || "?").slice(0, 2).toUpperCase();
  const top = h("header", { cls: "top" }, h("div", { cls: "top-in" },
    studioLevel
      ? h("a", { cls: "brand plain", href: "#/home", "aria-label": "Reel Studio dashboard" }, h("div", {}, h("b", {}, "Reel Studio"), h("span", {}, "All accounts")))
      : h("button", { cls: "brand acct", onclick: () => go("home"), title: "Switch account", "aria-label": `${brandOf().name}. Switch account` }, brandLogo(brandOf(), 36),
          h("div", {}, h("b", {}, brandOf().name), h("span", {}, "Switch account ▾"))),
    nav && S.session ? h("nav", { cls: "nav", "aria-label": "Main" }, items.map((it) => navBtn(it, false))) : null,
    h("div", { cls: "spacer" }), lamps,
    S.session ? h("button", { cls: "who", "aria-label": "Account menu", onclick: toggleMenu }, initials) : null));
  const tab = nav && S.session ? h("nav", { cls: "tabbar", "aria-label": "Main", style: `grid-template-columns:repeat(${items.length},1fr)` }, items.map((it) => navBtn(it, true))) : null;
  $app.replaceChildren(top, h("main", { id: "main" }, content), tab || "");
  updateTitle();
}
function toggleMenu(e) {
  e.stopPropagation(); const old = document.querySelector(".menu"); if (old) { old.remove(); return; }
  const m = h("div", { cls: "menu", role: "menu" },
    h("div", { cls: "who-line" }, S.session.user.email),
    h("button", { cls: "btn quiet", onclick: () => go("settings") }, icon("gear"), "Studio settings"),
    !STUDIO_ROUTES.includes(S.route) ? h("button", { cls: "btn quiet", onclick: () => go("account") }, `${brandOf().name} settings`) : null,
    h("button", { cls: "btn quiet", onclick: () => go("check") }, "Run pre-flight check"),
    "Notification" in window && Notification.permission === "default" ? h("button", { cls: "btn quiet", onclick: async () => { await askAlerts(); m.remove(); } }, icon("bell"), "Turn on ready alerts") : null,
    S.installEvt ? h("button", { cls: "btn quiet", onclick: async () => { S.installEvt.prompt(); S.installEvt = null; m.remove(); } }, "Install as app") : null,
    h("button", { cls: "btn quiet danger", onclick: () => sb.auth.signOut() }, "Sign out"));
  document.querySelector(".top").append(m);
  setTimeout(() => document.addEventListener("click", function close(ev) { if (!m.contains(ev.target)) { m.remove(); document.removeEventListener("click", close); } }), 0);
}

// ---------------------------------------------------------------- router
const STUDIO_ROUTES = ["home", "settings"];
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
  // only starting a reel needs the Mac: every other page (accounts, settings, review, library) works with the Mac and Claude offline
  if (r === "check" || (!fresh && r === "create")) return viewPreflight(r === "check" ? "home" : r);
  S.route = ["home", "settings", "create", "production", "library", "insights", "agents", "account"].includes(r) ? r : "home";
  ({ home: viewDashboard, settings: viewSettings, create: viewCreate, production: viewProduction, library: viewLibrary, insights: viewInsights, agents: viewAgents, account: viewAccount })[S.route]();
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
// ---------------------------------------------------------------- Performance: real Instagram results, charts and the weekly digest
// Account colours for charts: a validated categorical palette in a fixed order (the brand colours of We One and Book My
// Charter are both navy and could not be told apart). Identity is never colour alone: every bar and tile is labelled.
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const seriesOf = (bid) => SERIES[Math.max(0, (S.brands || BRANDS_FALLBACK).findIndex((b) => b.id === bid)) % SERIES.length];
const nfmt = (v) => v == null ? "–" : Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M" : Math.abs(v) >= 1e4 ? Math.round(v / 1e3) + "K" : Math.abs(v) >= 1e3 ? (v / 1e3).toFixed(1).replace(/\.0$/, "") + "K" : String(Math.round(v * 10) / 10);
const holdOf = (r) => r.st?.reels_skip_rate != null ? Math.round(100 - r.st.reels_skip_rate) : r.st?.avg_watch_s && r.dur ? Math.round(Math.min(1, r.st.avg_watch_s / r.dur) * 100) : null;
async function perfRows(bid) {         // posted reels (60 days) with their results; one query, cached for the page
  const since = new Date(Date.now() - 60 * 86400e3).toISOString();
  let q = sb.from("jobs").select("id,brand,status,created_at,title:output->>title,st:output->ig_stats,posted:output->ig->>posted_at,link:output->ig->>permalink,thumb:output->>thumb,hook:output->hook,dur:output->>duration")
    .eq("kind", "reel").gte("created_at", since).order("created_at", { ascending: false }).limit(400);
  if (bid) q = q.eq("brand", bid);
  const { data } = await q; return (data || []).map((r) => ({ ...r, dur: r.dur ? Number(r.dur) : null }));
}
const sumOf = (rows, k) => rows.reduce((a, r) => a + (Number(r.st?.[k]) || 0), 0);
const inWeek = (rows, w) => rows.filter((r) => r.posted && Date.parse(r.posted) > Date.now() - (w + 1) * 7 * 86400e3 && Date.parse(r.posted) <= Date.now() - w * 7 * 86400e3);
function kpiTiles(rows, dms) {
  const a = inWeek(rows, 0), b = inWeek(rows, 1);
  const tile = (label, k, extra) => { const v = k ? sumOf(a, k) : extra[0], pv = k ? sumOf(b, k) : extra[1];
    const d = pv ? Math.round(100 * (v - pv) / pv) : null;
    return h("div", { cls: "kpi" }, h("span", { cls: "kpi-l" }, label), h("b", { cls: "kpi-v" }, nfmt(v)),
      h("span", { cls: "kpi-d " + (d == null ? "" : d >= 0 ? "up" : "down") }, d == null ? (b.length ? "last week 0" : "no data last week") : `${d >= 0 ? "▲" : "▼"} ${Math.abs(d)}% vs last week`)); };
  return h("div", { cls: "kpis" }, tile("Reels posted", null, [a.length, b.length]), tile("Views", "views"), tile("Reach", "reach"), tile("Sends", "shares"), tile("Saves", "saved"),
    dms != null ? tile("DMs sent", null, dms) : null);
}
// tooltip shared by every chart
function vtip() { let t = document.querySelector(".vtip"); if (!t) { t = h("div", { cls: "vtip", role: "tooltip" }); document.body.append(t); } return t; }
function hover(el, html) {
  const show = (e) => { const t = vtip(); t.replaceChildren(...html()); t.style.display = "block";
    const x = Math.min(window.innerWidth - t.offsetWidth - 8, e.clientX + 12), y = Math.max(8, e.clientY - t.offsetHeight - 12); t.style.left = x + "px"; t.style.top = y + "px"; };
  el.addEventListener("mousemove", show); el.addEventListener("mouseenter", show); el.addEventListener("mouseleave", () => { vtip().style.display = "none"; });
  el.addEventListener("focus", (e) => { const r = el.getBoundingClientRect(); show({ clientX: r.left + r.width / 2, clientY: r.top }); }); el.addEventListener("blur", () => { vtip().style.display = "none"; });
}
const niceMax = (m) => { if (m <= 0) return 1; const p = 10 ** Math.floor(Math.log10(m)); const f = m / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p; };
// columns over time: one series, value at the tallest cap only, hover for every column
function columns({ items, color, unit = "", height = 220, label }) {
  // drawn at the real width of its box (no stretched text), redrawn when the box resizes
  const wrap = h("div", { cls: "chart-wrap" });
  const draw = (box) => {
    const W = Math.max(300, Math.round(box)), H = height, L = 40, B = 24, T = 18, max = niceMax(Math.max(...items.map((i) => i.value || 0), 1));
    const step = (W - L - 8) / items.length, bw = Math.max(6, Math.min(28, step - 4)), y = (v) => T + (H - T - B) * (1 - v / max);
    const every = Math.max(1, Math.ceil(52 / step));
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "vchart", role: "img", "aria-label": label });
    for (const t of [0, 0.5, 1]) { const v = max * t; svg.append(s("line", { x1: L, x2: W - 4, y1: y(v), y2: y(v), class: "grid" }), s("text", { x: L - 6, y: y(v) + 4, class: "tick", "text-anchor": "end" }, nfmt(v) + unit)); }
    const top = items.reduce((a, i, k) => (i.value || 0) > (items[a].value || 0) ? k : a, 0);
    items.forEach((it, k) => {
      const x = L + 4 + k * step + (step - bw) / 2, v = it.value || 0, yy = y(v), hh = Math.max(0, H - B - yy), r = Math.min(4, hh, bw / 2);
      const g = s("g", { tabindex: "0", class: "hit", "aria-label": `${it.label}: ${nfmt(it.value)}${unit}` });
      g.append(s("rect", { x: x - (step - bw) / 2 + 1, y: T, width: step - 2, height: H - B - T, fill: "transparent" }));
      if (it.value != null) g.append(s("path", { d: `M${x},${H - B} V${yy + r} q0,-${r} ${r},-${r} h${bw - 2 * r} q${r},0 ${r},${r} V${H - B} Z`, fill: color }));
      else g.append(s("text", { x: x + bw / 2, y: H - B - 4, class: "tick", "text-anchor": "middle" }, "–"));
      if (k === top && it.value) g.append(s("text", { x: x + bw / 2, y: yy - 5, class: "val", "text-anchor": "middle" }, nfmt(v) + unit));
      if ((items.length - 1 - k) % every === 0) g.append(s("text", { x: x + bw / 2, y: H - 7, class: "tick", "text-anchor": "middle" }, it.x));
      hover(g, it.tip); svg.append(g);
    });
    svg.append(s("line", { x1: L, x2: W - 4, y1: H - B, y2: H - B, class: "axis" }));
    wrap.replaceChildren(svg);
  };
  let last = 0;
  const ro = new ResizeObserver(([e]) => { const w = Math.round(e.contentRect.width); if (w && Math.abs(w - last) > 4) { last = w; draw(w); } });
  ro.observe(wrap); draw(640);
  return wrap;
}
// horizontal bars: items compared on one measure, labelled, value at the tip
function hbars({ items, unit = "", label, max }) {
  const mx = max || niceMax(Math.max(...items.map((i) => i.value || 0), 1));
  return h("div", { cls: "hbars", role: "list", "aria-label": label }, items.map((it) => {
    const row = h("div", { cls: "hbar", role: "listitem", tabindex: "0" }, h("span", { cls: "hbar-l", title: it.label }, it.label),
      h("span", { cls: "hbar-track" }, h("i", { style: `width:${it.value == null ? 0 : Math.max(1.5, 100 * it.value / mx)}%;background:${it.color}` })),
      h("b", { cls: "hbar-v" }, it.value == null ? "–" : nfmt(it.value) + unit));
    if (it.tip) hover(row, it.tip); return row; }));
}
const tipLines = (title, pairs) => [h("b", {}, title), ...pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => h("div", {}, h("span", { cls: "muted" }, k + " "), String(v)))];
// a reel tile for the digest (thumbnail kept after the video is removed)
function reelTile(r, tag) {
  const img = h("img", { alt: "", loading: "lazy" }); if (r?.thumb) signed(r.thumb).then((u) => { if (u) img.src = u; });
  return h("div", { cls: "rtile" }, h("span", { cls: "rtile-tag " + tag }, tag === "best" ? "Best reel" : "Weakest reel"),
    r ? [h("div", { cls: "rtile-img" }, img), h("div", {}, h("b", {}, r.title || "Untitled"),
      h("p", { cls: "small muted" }, [r.views != null ? `${nfmt(r.views)} views` : null, r.hold != null ? `${r.hold}% held the opening` : null, r.shares != null ? `${r.shares} sends` : null, r.saved != null ? `${r.saved} saves` : null].filter(Boolean).join(" · ")),
      r.opening ? h("p", { cls: "tiny faint" }, `Opening: “${String(r.opening).slice(0, 90)}”`) : null,
      r.permalink ? h("a", { cls: "tiny", href: r.permalink, target: "_blank", rel: "noopener noreferrer" }, "Open on Instagram") : null)]
      : h("p", { cls: "small muted" }, "Not enough results this week."));
}
function digestPanel(b, dg) {
  if (!dg) return h("section", { cls: "panel digest" }, h("h3", {}, "Weekly digest"), h("p", { cls: "small muted" }, "Written by the daily learning run (06:05). The first digest appears after its next run."));
  return h("section", { cls: "panel digest" },
    h("div", { cls: "digest-head" }, h("h3", {}, `Weekly digest · ${b.name}`), h("span", { cls: "tiny faint" }, `${dateShort(dg.week_start)} – ${dateShort(dg.at)} · updated ${ago(dg.at)}`)),
    dg.evidence === "weak" ? h("p", { cls: "tiny", style: "color:#6E4300" }, `Only ${dg.measured} reels with results: treat these as hints, not proof.`) : null,
    h("div", { cls: "rtiles" }, reelTile(dg.best, "best"), dg.worst ? reelTile(dg.worst, "worst") : null),
    dg.insights ? h("p", { cls: "small", style: "margin-top:10px" }, dg.insights) : null,
    h("div", { cls: "digest-cols" },
      h("div", {}, h("h4", {}, "What the system learned"), dg.lessons?.length ? h("ul", { cls: "small" }, dg.lessons.map((l) => h("li", {}, l.lesson, h("span", { cls: "tiny faint" }, ` · ${l.agent.replace("_", " ")}, ${l.status}`))))
        : h("p", { cls: "small muted" }, "No new lessons this week.")),
      h("div", {}, h("h4", {}, "Next experiment"), h("p", { cls: "small" }, dg.next_experiment))));
}
async function viewInsights() {
  const body = h("div", {}, h("div", { cls: "skeleton", style: "height:320px" }));
  shell(h("div", {}, h("div", { cls: "page-head" }, h("div", {}, h("h1", {}, "Insights"),
    h("p", {}, `${brandOf().name}: real results from Instagram, what holds viewers in the first seconds, and what the system is learning.`))), body));
  const b = brandOf();
  const [rows] = await Promise.all([perfRows(b.id), loadSettings().catch(() => {})]);
  const since = new Date(Date.now() - 7 * 86400e3).toISOString(), prev = new Date(Date.now() - 14 * 86400e3).toISOString();
  const dmr = (await sb.from("dm_log").select("created_at").eq("brand", b.id).eq("state", "sent").gte("created_at", prev).then((r) => r.data, () => [])) || [];
  const dms = [dmr.filter((r) => r.created_at >= since).length, dmr.filter((r) => r.created_at < since).length];
  const posted = rows.filter((r) => r.posted).sort((x, y) => Date.parse(x.posted) - Date.parse(y.posted)).slice(-24);
  const color = seriesOf(b.id);
  const viewsChart = posted.length ? columns({ label: "Views per reel", color, items: posted.map((r) => ({ x: dateShort(r.posted), label: r.title, value: r.st?.views ?? null,
    tip: () => tipLines(r.title || "Reel", [["Posted", tzClock(r.posted)], ["Views", r.st?.views], ["Reach", r.st?.reach], ["Sends", r.st?.shares], ["Saves", r.st?.saved], ["Held opening", holdOf(r) != null ? holdOf(r) + "%" : null]]) })) })
    : h("p", { cls: "small muted" }, "No posted reels in the last 60 days.");
  const hooks = posted.filter((r) => holdOf(r) != null && (r.hook?.say || r.title)).sort((x, y) => holdOf(y) - holdOf(x));
  const hookChart = hooks.length ? hbars({ label: "Openings ranked by how many viewers stayed", unit: "%", max: 100, items: hooks.slice(0, 10).map((r) => ({ label: r.hook?.text || r.hook?.say || r.title, value: holdOf(r), color,
    tip: () => tipLines(r.title || "Reel", [["Says", r.hook?.say], ["On screen", r.hook?.text], ["First shot", r.hook?.visual], ["Measure", r.st?.reels_skip_rate != null ? "100 − skip rate (first 3 s)" : "share of the reel watched"]]) })) })
    : h("p", { cls: "small muted" }, "Hold rates appear about a day after reels are posted (Instagram needs the insights permission).");
  const lib = (S.settings?.[bkey("ig_library", b.id)]?.posts || []);
  const byType = ["REELS", "FEED"].map((t) => { const ps = lib.filter((p) => (p.type || "FEED") === t); return { t, n: ps.length, likes: ps.length ? Math.round(ps.reduce((a, p) => a + (p.likes || 0), 0) / ps.length) : null }; });
  const table = h("details", { cls: "more" }, h("summary", {}, "Table view"), h("table", { cls: "dtable" }, h("thead", {}, h("tr", {}, ["Posted", "Reel", "Views", "Reach", "Sends", "Saves", "Held"].map((c) => h("th", {}, c)))),
    h("tbody", {}, [...posted].reverse().map((r) => h("tr", {}, h("td", {}, dateShort(r.posted)), h("td", {}, r.title || ""), ...["views", "reach", "shares", "saved"].map((k) => h("td", { cls: "num" }, nfmt(r.st?.[k] ?? null))), h("td", { cls: "num" }, holdOf(r) != null ? holdOf(r) + "%" : "–"))))));
  body.replaceChildren(
    kpiTiles(rows, dms),
    digestPanel(b, S.settings?.[bkey("digest", b.id)]),
    h("section", { cls: "panel" }, h("h3", {}, "Views per reel"), h("p", { cls: "small muted" }, "Last 24 posted reels, oldest left. Hover a column for its numbers."), viewsChart, table),
    h("section", { cls: "panel" }, h("h3", {}, "Hook bank"), h("p", { cls: "small muted" }, "Each reel's opening ranked by how many viewers stayed past the first seconds. The scriptwriter copies the patterns of the strongest, never the words."), hookChart),
    lib.length ? h("section", { cls: "panel" }, h("h3", {}, "The account's other posts"), h("p", { cls: "small muted" }, `${lib.length} recent Instagram posts the learning also reads (refreshed daily).`),
      hbars({ label: "Average likes by post type", items: byType.filter((x) => x.n).map((x) => ({ label: `${x.t === "REELS" ? "Reels" : "Photos & carousels"} (${x.n})`, value: x.likes, color })) })) : null);
}
// ---------------------------------------------------------------- Dashboard: every account at a glance (first page after sign-in)
async function viewDashboard() {
  const grid = h("div", { cls: "accts" }, [1, 2, 3, 4].map(() => h("div", { cls: "skeleton acct-skel" })));
  const strip = h("div", { cls: "dash-strip" });
  const perf = h("div", { cls: "perf" });
  shell(h("div", {}, h("div", { cls: "page-head" }, h("div", {}, h("h1", {}, "Dashboard"), h("p", {}, "Every account in one studio. Same process, each with its own reels, posting and autopilot."))), strip, grid, perf));
  homePerf(perf).catch(() => perf.replaceChildren());
  const paint = async () => {
    if (!grid.isConnected) return;
    const since = new Date(Date.now() - 30 * 86400e3).toISOString(); const week = Date.now() - 7 * 86400e3;
    const [, { data: rows }, , { data: ap }] = await Promise.all([loadBrands(),
      sb.from("jobs").select("id,brand,status,created_at,updated_at,ig:output->ig->>state,posted:output->ig->>posted_at").eq("kind", "reel").gte("created_at", since).limit(500),
      loadSettings().catch(() => {}),
      sb.from("jobs").select("status,brand,input").eq("kind", "suggest").eq("input->>autopilot", "true").order("created_at", { ascending: false }).limit(40)]);
    const all = rows || []; const st = (b) => all.filter((j) => (j.brand || "weone") === b);
    const postedWeek = (list) => list.filter((j) => j.ig === "posted" && j.posted && Date.parse(j.posted) > week).length;
    const apLast = (bid) => (ap || []).find((j) => (j.brand || "weone") === bid);
    const apOn = (S.brands || BRANDS_FALLBACK).filter((b) => b.status === "live" && apOf(b.id).on).length;
    strip.replaceChildren(...[
      h("span", { cls: "pill" }, h("i", { cls: "lamp " + (macAlive() ? "ok" : "bad") }), macAlive() ? "Studio Mac online" : "Studio Mac offline"),
      macAlive() ? h("span", { cls: "pill" }, h("i", { cls: "lamp " + (S.worker?.claude_ok ? "ok" : "bad") }), S.worker?.claude_ok ? "Claude ready" : "Claude not ready") : null,
      h("span", { cls: "pill" }, `${postedWeek(all)} posted this week`),
      h("span", { cls: "pill" }, apOn ? `Autopilot on for ${apOn} account${apOn > 1 ? "s" : ""}` : "Autopilot off")].filter(Boolean));
    const plat = (b) => {
      const dot = (k, label) => { const v = k === "web" ? stOf("website_status", b.id) : stOf(PST[k], b.id); const state = v.state || "not_connected";
        const word = k === "web" && state === "connected" ? "online" : state.replace("_", " ");
        const tip = `${label}: ${word}${v.note && state !== "connected" ? ". " + v.note : ""}`;
        return h("span", { cls: `mini ${k} ${state === "connected" ? "ok" : state === "not_connected" ? "no" : "warn"}`, title: tip, role: "img", "aria-label": tip },
          h("img", { src: PLAT_IMG[k], alt: "", width: 24, height: 24 })); };
      return h("button", { cls: "minis linkless", onclick: () => { setBrand(b.id); go("account"); }, "aria-label": `${b.name} connections` },
        dot("ig", "Instagram"), dot("fb", "Facebook"), dot("li", "LinkedIn"), safeUrl(b.website) ? dot("web", "Website") : null);
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
        live ? (() => { const a = apOf(b.id); const run = apLast(b.id); const busy = run && ACTIVE.includes(run.status);
          return h("div", { cls: "acct-ap" }, apSwitch(b, () => paint().catch(() => {})),
            h("span", { cls: "small muted" }, busy ? "Running now…" : a.on ? `Daily ${slotLabel(a.time || "13:30")} · next ${tzClock(nextRun(a, run))}` : "Off · tap to choose a daily time"),
            a.on && !anyPosting(b.id) ? h("span", { cls: "tiny", style: "color:#6E4300" }, "Nothing connected: reels wait for you") : null); })() : null,
        h("div", { cls: "acct-foot" }, plat(b),
          safeUrl(b.website) ? h("a", { cls: "acct-web", href: b.website, target: "_blank", rel: "noopener noreferrer" }, h("img", { src: PLAT_IMG.web, alt: "", width: 16, height: 16 }), host(b.website)) : null,
          h("button", { cls: "btn quiet", onclick: () => { setBrand(b.id); go("account"); }, "aria-label": `Settings of ${b.name}` }, icon("gear", 16), "Settings"),
          h("button", { cls: "btn " + (live ? "primary" : "quiet"), onclick: open }, "Open")));
    }), h("button", { cls: "acct add-acct", onclick: () => addAccountDialog(paint), "aria-label": "Add an account" },
      h("span", { cls: "add-plus", "aria-hidden": "true" }, "+"), h("b", {}, "Add account"),
      h("span", { cls: "small muted" }, "Account Intelligence studies its website, socials, competitors and market, then writes its strategy."))]);
  };
  await paint().catch(() => {});
  S.timers.push(setInterval(() => paint().catch(() => {}), 15000));
}
// home: how every account is doing, side by side (refreshed when the page opens; results change a few times a day)
async function homePerf(box) {
  box.replaceChildren(h("div", { cls: "skeleton", style: "height:220px;margin-top:20px" }));
  const prev = new Date(Date.now() - 14 * 86400e3).toISOString(), since = new Date(Date.now() - 7 * 86400e3).toISOString();
  const [rows, dmr] = await Promise.all([perfRows(), sb.from("dm_log").select("brand,created_at").eq("state", "sent").gte("created_at", prev).then((r) => r.data || [], () => [])]);
  await loadSettings().catch(() => {});
  const live = (S.brands || BRANDS_FALLBACK).filter((b) => b.status === "live");
  const mine = (bid) => rows.filter((r) => (r.brand || "weone") === bid);
  const posted30 = (bid) => mine(bid).filter((r) => r.posted && Date.parse(r.posted) > Date.now() - 30 * 86400e3 && r.st?.views != null);
  const avg = (list, k) => list.length ? list.reduce((a, r) => a + (Number(r.st?.[k]) || 0), 0) / list.length : null;
  const bars = (k, unit) => hbars({ label: k, unit, items: live.map((b) => { const ps = posted30(b.id); const v = avg(ps, k);
    return { label: b.name, value: v == null ? null : Math.round(v), color: seriesOf(b.id),
      tip: () => tipLines(b.name, [["Reels with results (30 days)", ps.length], ["Average views", nfmt(avg(ps, "views"))], ["Average reach", nfmt(avg(ps, "reach"))], ["Average sends", avg(ps, "shares")?.toFixed(1)], ["Average saves", avg(ps, "saved")?.toFixed(1)]]) }; }) });
  const holdBars = hbars({ label: "Viewers who stayed past the opening", unit: "%", max: 100, items: live.map((b) => { const hs = posted30(b.id).map(holdOf).filter((v) => v != null);
    return { label: b.name, value: hs.length ? Math.round(hs.reduce((a, v) => a + v, 0) / hs.length) : null, color: seriesOf(b.id), tip: () => tipLines(b.name, [["Reels measured", hs.length]]) }; }) });
  const card = (b) => { const dg = S.settings?.[bkey("digest", b.id)];
    return h("button", { cls: "dg-card", onclick: () => { setBrand(b.id); go("insights"); }, "aria-label": `${b.name} insights` },
      h("div", { cls: "dg-top" }, h("i", { cls: "dg-dot", style: `background:${seriesOf(b.id)}` }), h("b", {}, b.name), h("span", { cls: "tiny faint" }, dg ? `updated ${ago(dg.at)}` : "no digest yet")),
      dg ? [h("p", { cls: "small" }, h("span", { cls: "muted" }, "Best: "), dg.best ? `${dg.best.title || "Untitled"}${dg.best.views != null ? ` · ${nfmt(dg.best.views)} views` : ""}` : "not enough results"),
        h("p", { cls: "small" }, h("span", { cls: "muted" }, "Next experiment: "), dg.next_experiment || "–"),
        dg.evidence === "weak" ? h("span", { cls: "tiny", style: "color:#6E4300" }, `Early signal: ${dg.measured} reels measured`) : null]
        : h("p", { cls: "small muted" }, "Written by the next daily learning run."),
      h("span", { cls: "tiny dg-more" }, "Open insights →")); };
  box.replaceChildren(h("div", { cls: "page-head", style: "margin-top:28px" }, h("div", {}, h("h2", {}, "Performance"), h("p", { cls: "small muted" }, "Instagram results of every account. Totals compare the last 7 days with the 7 before."))),
    kpiTiles(rows, [dmr.filter((r) => r.created_at >= since).length, dmr.filter((r) => r.created_at < since).length]),
    h("div", { cls: "perf-grid" },
      h("section", { cls: "panel" }, h("h3", {}, "Average views per reel"), h("p", { cls: "small muted" }, "Posted reels of the last 30 days."), bars("views", "")),
      h("section", { cls: "panel" }, h("h3", {}, "Opening hold"), h("p", { cls: "small muted" }, "Share of viewers still watching after the first seconds."), holdBars)),
    h("h3", { style: "margin:22px 0 10px" }, "Weekly digest"),
    h("div", { cls: "dg-grid" }, live.map(card)));
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
function nextRun(ap, last = S.apLast) {
  const [hh, mm] = String(ap.time || "13:30").split(":").map(Number);
  const nowIst = new Date(Date.now() + 330 * 60e3); const t = new Date(Date.UTC(nowIst.getUTCFullYear(), nowIst.getUTCMonth(), nowIst.getUTCDate(), hh, mm) - 330 * 60e3);
  return t.getTime() + 3 * 3600e3 < Date.now() || (last?.input?.day === nowIst.toISOString().slice(0, 10) && last?.input?.trigger === "schedule") ? new Date(t.getTime() + 86400e3) : t;
}
// each account has its own Autopilot ('autopilot' for We One Aviation, 'autopilot@<account>' for the others; off until switched on)
// an example that fits each account (the box steers the topic research)
const STEER_EX = { weone: "DGCA exam tips, cockpit secrets", flystar: "CPL ground subjects, ATPL exam tips",
  bookmycharter: "Char Dham by helicopter, private jet costs", legalgroup: "tenant rights, property disputes" };
const apOf = (b = S.brand) => S.settings?.[bkey("autopilot", b)] || { on: false, time: "" };
const AP_SLOTS = ["13:30", "15:30", "17:30", "19:30", "11:30", "21:00"];
function apSuggest(bid) {             // a free slot, at least 90 min from the other accounts' runs, so the Mac makes one reel at a time
  const mins = (t) => { const [h_, m_] = t.split(":").map(Number); return h_ * 60 + m_; };
  const used = (S.brands || BRANDS_FALLBACK).filter((x) => x.id !== bid && apOf(x.id).on).map((x) => mins(apOf(x.id).time || "13:30"));
  return AP_SLOTS.find((t) => used.every((u) => Math.abs(u - mins(t)) >= 90)) || "13:30";
}
// switch Autopilot on: always asks for the daily time first
function apDialog(b, after) {
  const ap = apOf(b.id); const time = h("input", { type: "time", value: ap.time || apSuggest(b.id), "aria-label": "Daily time (India)", style: "width:150px;margin-top:6px" });
  const msg = statusMsg(); const posts = anyPosting(b.id);
  const go_ = h("button", { cls: "btn primary", onclick: async () => {
    if (!/^\d\d:\d\d$/.test(time.value)) { time.focus(); return; }
    go_.disabled = true; msg.textContent = "Saving…";
    try { await rpc("set_autopilot", { p_on: true, p_time: time.value, p_brand: b.id }); await loadSettings();
      toast(`Autopilot on for ${b.name}: every day at ${slotLabel(time.value)}.`); d.close(); after?.(); }
    catch (e) { msg.textContent = errText(e); go_.disabled = false; } } }, "Turn on");
  const d = h("dialog", { "aria-label": `Autopilot for ${b.name}`, cls: "connect" }, h("div", { style: "padding:20px 20px 4px" },
    h("h2", { style: "font-size:22px" }, `Autopilot for ${b.name}`),
    h("p", { cls: "small muted", style: "margin:6px 0 12px" }, "Every day at this time it researches what is trending, picks the topic most likely to perform, makes the reel and posts it. Nobody has to tap anything."),
    h("label", { cls: "fld" }, h("span", {}, "Daily time (India)"), time),
    h("p", { cls: "tiny faint", style: "margin-top:6px" }, "Suggested: a time at least 90 minutes away from the other accounts, so the Mac makes one reel at a time."),
    posts ? null : h("p", { cls: "small", style: "margin-top:10px;color:#6E4300" }, "Nothing is connected for this account yet, so its reels will wait in Production for you to post. Connect Instagram, Facebook or LinkedIn in its Settings to post automatically."),
    msg), h("div", { cls: "dlg-foot" }, h("button", { cls: "btn quiet", onclick: () => d.close() }, "Cancel"), go_));
  d.addEventListener("close", () => d.remove()); document.body.append(d); d.showModal(); time.focus();
}
async function apToggle(b, on, after) {
  if (on) return apDialog(b, after);
  try { await rpc("set_autopilot", { p_on: false, p_time: apOf(b.id).time || "13:30", p_brand: b.id }); await loadSettings(); toast(`Autopilot off for ${b.name}.`); }
  catch (e) { toast(errText(e), { err: true }); }
  after?.();
}
const apSwitch = (b, after, label = "Autopilot") => { const ap = apOf(b.id);
  return h("button", { cls: "switch" + (ap.on ? " on" : ""), role: "switch", "aria-checked": String(!!ap.on), "aria-label": `${label} for ${b.name}`,
    onclick: (e) => { e.stopPropagation(); apToggle(b, !ap.on, after); } }, h("i", { "aria-hidden": "true" }), h("span", {}, label)); };
function autopilotPanel() {
  const box = h("section", { cls: "panel ap", "aria-label": "Autopilot" });
  const paint = async () => {
    if (!box.isConnected && box._painted) return;
    const bid = S.brand; const b = brandOf();
    const [{ data }] = await Promise.all([sb.from("jobs").select("id,status,input,output,error,updated_at,created_at").eq("kind", "suggest").eq("brand", bid).eq("input->>autopilot", "true").order("created_at", { ascending: false }).limit(1), loadSettings().catch(() => {})]);
    if (bid !== S.brand) return;
    const j = (data || [])[0]; S.apLast = j; box._painted = true;
    const ap = apOf(bid); const ins = S.settings?.[bkey("instagram_insights", bid)];
    const ed = S.apEdit; const o = j?.output || {}; const pick = o.autopilot;
    let lastLine = null, ev = null;
    if (j && ACTIVE.includes(j.status)) { ev = (await events(j.id, 1).catch(() => []))[0];
      lastLine = h("p", { cls: "small", style: "margin-top:8px" }, h("i", { cls: "lamp wait" }), j.status === "queued" ? (macAlive() ? "Starting…" : "Queued. It starts when the Mac is online.") : j.status === "waiting_quota" ? `Claude usage limit; continues ${j.not_before ? "at " + clock(j.not_before) : "later"}.` : (ev?.message || "Researching today's trends…")); }
    else if (j && j.status === "done" && pick?.skipped) lastLine = h("p", { cls: "small muted", style: "margin-top:8px" }, pick.skipped === "off" ? `The ${pick.day} run was skipped: Autopilot was switched off.` : `The ${pick.day} run was skipped: the Mac was offline at that time.`);
    else if (j && j.status === "done" && pick) lastLine = h("div", { cls: "ap-pick" },
      h("p", { cls: "tiny faint" }, `Last pick, ${ago(j.updated_at)} · score ${pick.score}/100 · ${String(pick.category || "").replace("_", " ")}`),
      h("h3", {}, pick.title), h("p", { cls: "hook" }, pick.hook), pick.why ? h("p", { cls: "small muted" }, pick.why) : null,
      h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:8px" }, h("button", { cls: "btn", onclick: () => go("production", pick.reel_id) }, "Follow this reel"),
        (o.topics || []).length ? h("details", { cls: "more" }, h("summary", {}, `All ${o.topics.length} topics it scored`), h("ol", { cls: "ap-board" }, o.topics.map((t) =>
          h("li", {}, h("b", { cls: "num" }, t.score == null ? (t.repeat ? "repeat" : "unsafe") : `${t.score}`), " ", t.title, t.editor_note ? h("span", { cls: "tiny faint" }, ` · ${t.editor_note}`) : null)))) : null));
    else if (j && ["failed", "cancelled"].includes(j.status)) lastLine = h("p", { cls: "small", style: "margin-top:8px;color:#7D1C26" }, `Last run ${j.status === "failed" ? "failed" : "was stopped"}: ${j.error || ""}`);
    const timeIn = h("input", { type: "time", value: ed?.time || ap.time || "13:30", "aria-label": "Daily time (India)", style: "width:130px", oninput: (e) => { S.apEdit = { ...(S.apEdit || ap), time: e.target.value }; } });
    const save = async (on, time) => { try { await rpc("set_autopilot", { p_on: on, p_time: time, p_brand: bid }); await loadSettings(); S.apEdit = null; toast(on ? `Autopilot on: every day at ${slotLabel(time)}.` : "Autopilot off."); } catch (e) { toast(errText(e), { err: true }); } paint(); };
    const running = j && ACTIVE.includes(j.status);
    box.replaceChildren(
      h("div", { cls: "igp-head" },
        h("div", {}, h("h3", {}, h("i", { cls: "lamp " + (ap.on ? "ok" : "") }), ap.on ? `Autopilot is on: every day at ${slotLabel(ap.time || "13:30")}` : "Autopilot is off"),
          h("p", { cls: "small muted", style: "margin-top:4px" }, "Picks today's most viral-ready topic, makes the reel and posts it. No taps needed."),
          ap.on && !running ? h("p", { cls: "tiny faint", style: "margin-top:4px" }, `Next run ${tzClock(nextRun(ap))}.`) : null,
          ins && ins.ok === false ? h("p", { cls: "tiny", style: "margin-top:4px;color:#6E4300" }, "Learning uses likes and comments only. Add the instagram_business_manage_insights permission to the Instagram token for views, sends and saves.") : null),
        h("div", { cls: "igp-btns" },
          ed ? [timeIn, h("button", { cls: "btn primary", onclick: () => save(ap.on, timeIn.value || "13:30") }, "Save"), h("button", { cls: "btn quiet", onclick: () => { S.apEdit = null; paint(); } }, "Cancel")]
             : [h("button", { cls: "btn primary", disabled: !!running, onclick: async (e) => { e.currentTarget.disabled = true; try { await rpc("run_autopilot_now", { p_brand: bid }); toast("Autopilot started. It picks a topic in a few minutes, then makes the reel."); } catch (err) { toast(errText(err), { err: true }); } paint(); } }, running ? "Running…" : "Run now"),
                ap.on ? h("button", { cls: "btn quiet", onclick: () => { S.apEdit = { ...ap }; paint(); } }, "Change time") : null,
                apSwitch(b, () => paint().catch(() => {}))])),
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
    isLive() ? autopilotPanel() : null,
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
    if (!t) { plan.replaceChildren(h("h3", {}, S.brand === "legalgroup" ? "Reel plan" : "Flight plan"), h("p", { cls: "plan-empty small" }, "Pick a topic to see when it will be ready."), h("div", { cls: "plan-row" }, h("span", { cls: "faint small" }, `Typical reel: ${mins(totalSec())}`), go_)); return; }
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
    const steer = h("input", { placeholder: `Optional: steer the ideas, e.g. ${STEER_EX[S.brand] || "a service, a season or a current event"}`, "aria-label": "Steer the ideas", value: S.steer, oninput: (e) => { S.steer = e.target.value; } });
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
const ownPosting = (j) => anyPosting((j && j.brand) || S.brand);          // only an account's own connected platforms, never another's
const autoOn = (j) => ownPosting(j) && S.settings?.post_schedule?.auto !== false;
const nightIST = () => { const d = new Date(Date.now() + 330 * 60e3); const m = d.getUTCHours() * 60 + d.getUTCMinutes(); return m < 450 || m > 1380; };   // 23:00-07:30 India time
const nightWait = (j) => j.input?.autopilot && nightIST();
function autoPostLine(j) {
  const d = j.output?.review_deadline;
  if (j.output?.autopilot_hold) return "Ready. Autopilot held it back: approve it to post.";
  if (autoOn(j) && nightWait(j)) return "Ready. Autopilot posts it at 7:30 AM (never at night) unless you act.";
  if (!ownPosting(j)) return `Ready for your review. It goes to the Library only: ${brandOf(j.brand).name} has no Instagram, Facebook or LinkedIn connected yet.`;
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
  const [url, poster, dl] = await Promise.all([videoUrl(o), signed(o.thumb), videoUrl(o, name)]);
  const video = url ? h("video", { src: url, poster, controls: true, playsinline: true, preload: "metadata" }) : h("p", { cls: "muted", style: "color:#fff;padding:20px" }, "Video not available yet. It appears here once the upload finishes.");
  if (url) video.addEventListener("error", async () => { if ((video._re = (video._re || 0) + 1) > 3) return; const t = video.currentTime; const u = await videoUrl(o); if (u) { video.src = u; video.currentTime = t; } });
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
  const approve = h("button", { cls: "btn go", disabled: !!busy, onclick: () => act(j, "approve", () => rpc("set_review_decision", { p_job: j.id, p_decision: "approve", p_notes: null }), (row) => row?.output?.ig?.state ? "Approved. Posting to its connected pages now; the links appear in Library." : ownPosting(j) ? "Approved. It's in the Library." : `Approved. It's in ${brandOf(j.brand).name}'s Library (nothing was posted).`) },
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
      h("div", { cls: "decide" }, changeBox, h("div", { cls: "row" }, dl ? h("button", { cls: "btn quiet", onclick: () => downloadReel(o, name) }, icon("down"), "Download") : null, cancelChange, change, approve))));
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
  if (!(platSet(p, j.brand) || (p === "ig" && (j.brand || "weone") === "weone"))) return h("p", { cls: "ig-note" }, `Connect ${brandOf(j.brand).name}'s own ${PLAT[p]} in Settings to post. Reels never go to another account.`);
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
// ---------------------------------------------------------------- connections: each account's own Instagram, Facebook, LinkedIn
// Tokens go in and never come out: the site only ever sees status, the connected page's name and the last 4 characters.
const LI_SCOPES = "w_organization_social r_organization_social rw_organization_admin";
const LI_REDIRECT = location.origin + "/";
const armed = (e, label, fn) => { const b = e.currentTarget; if (!b._armed) { b._armed = true; b.textContent = label; setTimeout(() => { b._armed = false; }, 6000); return; } fn(); };
function liSignIn(clientId, b = S.brand) {
  const state = Array.from(crypto.getRandomValues(new Uint8Array(16)), (x) => x.toString(16).padStart(2, "0")).join("");
  try { sessionStorage.setItem("li_state", state); sessionStorage.setItem("li_brand", b); } catch { /* */ }
  location.href = "https://www.linkedin.com/oauth/v2/authorization?" + new URLSearchParams({ response_type: "code", client_id: clientId, redirect_uri: LI_REDIRECT, state, scope: LI_SCOPES });
}
function connCard(k, b, refresh) {
  const st = stOf(PST[k], b.id); const state = st.state || "not_connected"; const app = S.settings?.linkedin_app || {};
  const days = k === "li" && st.expires_at ? Math.floor((Date.parse(st.expires_at) - Date.now()) / 86400e3) : null;
  const kind = state === "connected" ? (days != null && days < 7 ? "warn" : "ok") : state === "not_connected" ? "no" : state === "checking" ? "wait" : "warn";
  const who = state === "connected" ? (k === "ig" ? `@${st.username}` : k === "fb" ? st.page_name : st.org_name) : "";
  const word = { ok: `Connected${who ? ": " + who : ""}`, no: "Not connected", wait: `Checking with ${PLAT[k]}…`, warn: state === "duplicate" ? "Belongs to another account" : state === "expired" ? "Connection expired" : days != null && days < 7 ? `Reconnect by ${dateShort(st.expires_at)}` : "Needs attention" }[kind];
  const owner = S.owner;
  const connect = () => (k === "ig" ? igDialog(b, refresh) : k === "fb" ? fbDialog(b, refresh) : liDialog(b, refresh));
  const disconnect = (e) => armed(e, "Tap again to disconnect", async () => {
    try { await rpc({ ig: "disconnect_instagram", fb: "disconnect_facebook", li: "disconnect_linkedin" }[k], { p_brand: b.id }); await loadSettings(); toast(`${PLAT[k]} disconnected from ${b.name}. Nothing will be posted there.`); }
    catch (err) { toast(errText(err), { err: true }); } refresh(); });
  const note = st.note && state !== "connected" ? st.note
    : k === "li" && state === "connected" && days != null ? `LinkedIn asks for a fresh sign-in every 60 days: by ${dateShort(st.expires_at)}.`
    : k === "fb" && state === "connected" ? "Page connections do not expire." : k === "ig" && state === "connected" ? "Renews itself automatically." : null;
  return h("article", { cls: `svc conn ${kind}` },
    h("div", { cls: "svc-head" }, h("span", { cls: "plat-tile svc-ico" }, h("img", { src: PLAT_IMG[k], alt: "" }), h("span", { cls: `plat-badge ${kind}`, "aria-hidden": "true" }, kind === "ok" ? "✓" : kind === "no" ? "✕" : kind === "wait" ? "…" : "!")),
      h("div", {}, h("h3", {}, PLAT[k]), h("p", { cls: `small plat-state ${kind}` }, word))),
    note ? h("p", { cls: "small muted" }, note) : null,
    st.checked_at && state !== "not_connected" ? h("p", { cls: "tiny faint" }, `Last checked ${ago(st.checked_at)}`) : null,
    !owner && state !== "not_connected" ? h("div", { cls: "svc-btns" }, recheckBtn(k, b, refresh)) : null,
    owner ? h("div", { cls: "svc-btns" }, state !== "not_connected" ? recheckBtn(k, b, refresh) : null,
      k === "li" && app.client_id && state !== "not_connected" ? h("button", { cls: kind === "ok" ? "btn quiet" : "btn primary", onclick: () => liSignIn(app.client_id, b.id) }, "Reconnect") : null,
      !(k === "li" && app.client_id && state !== "not_connected") ? h("button", { cls: kind === "ok" ? "btn quiet" : "btn primary", onclick: connect }, state === "not_connected" ? `Connect ${PLAT[k]}` : "Reconnect") : null,
      state !== "not_connected" ? h("button", { cls: "btn quiet danger", onclick: disconnect }, "Disconnect") : null)
      : h("p", { cls: "tiny faint" }, "Only the studio owner can connect or disconnect."));
}
// "Re-check": the cloud checks the connection within a minute (works while the Mac is offline) and the card updates by itself
const CHECK_LABEL = { ig: "Instagram", fb: "Facebook", li: "LinkedIn", web: "Website" };
function recheckBtn(k, b, refresh) {
  const base = k === "web" ? "website_status" : PST[k];
  const pending = () => { const v = stOf(base, b.id); return v.recheck_at && Date.now() - Date.parse(v.recheck_at) < 3 * 60e3; };
  return h("button", { cls: "btn quiet", disabled: !!pending(), title: "Ask the cloud to check this connection now", onclick: async (e) => {
    const btn = e.currentTarget; btn.disabled = true; btn.textContent = "Checking…";
    try {
      await rpc("request_recheck", { p_brand: b.id, p_platform: k }); await loadSettings();
      const t0 = Date.now(); let done = false;
      while (Date.now() - t0 < 100e3 && btn.isConnected) { await sleep(4000); await loadSettings().catch(() => {}); if (!stOf(base, b.id).recheck_at) { done = true; break; } }
      const v = stOf(base, b.id); const s_ = v.state || "not_connected";
      toast(!done ? `${CHECK_LABEL[k]}: still checking. The card updates by itself.`
        : s_ === "connected" ? `${CHECK_LABEL[k]} of ${b.name}: ${k === "web" ? "online" : "connected and working"}.`
        : `${CHECK_LABEL[k]} of ${b.name}: ${v.note || s_.replace("_", " ")}`, { err: done && s_ !== "connected" && s_ !== "not_connected" });
    } catch (err) { toast(errText(err), { err: true }); }
    refresh();
  } }, pending() ? "Checking…" : "Re-check");
}
// Comment-to-DM: a viewer comments the keyword, the account sends them one private message (Instagram allows one per comment, within 7 days)
const DM_STATE = { ready: ["ok", "Working: replies within about 2 minutes"], checking: ["wait", "Checking with Instagram…"], needs_permission: ["warn", "Needs a permission on the Instagram token"],
  not_connected: ["no", "Waiting for this account's Instagram"], error: ["warn", "Not working right now"] };
function dmCard(b, log, refresh) {
  const d = S.settings?.[bkey("dm", b.id)] || { on: false, keywords: [], message: "" };
  const [kind, word] = d.on ? (DM_STATE[d.state] || DM_STATE.checking) : ["no", "Off"];
  const kw = h("input", { type: "text", maxlength: "15", value: (d.keywords || [])[0] || "", placeholder: "Keyword, e.g. QUOTE", "aria-label": "Keyword", style: "margin-top:8px;text-transform:uppercase" });
  const msgIn = h("textarea", { rows: 5, maxlength: "900", "aria-label": "Message sent to the commenter", style: "margin-top:8px;width:100%" }, d.message || "");
  const pub = h("input", { type: "checkbox", checked: d.public_reply !== false ? true : null });
  const on = h("input", { type: "checkbox", checked: d.on ? true : null });
  const msg = statusMsg(); const sent = log.filter((r) => r.state === "sent");
  return h("article", { cls: `svc conn ${kind}`, style: "grid-column:1/-1" },
    h("div", { cls: "svc-head" }, h("span", { cls: "plat-tile svc-ico" }, h("img", { src: PLAT_IMG.ig, alt: "" }),
        h("span", { cls: `plat-badge ${kind}`, "aria-hidden": "true" }, kind === "ok" ? "✓" : kind === "no" ? "✕" : kind === "wait" ? "…" : "!")),
      h("div", {}, h("h3", {}, "Comment-to-DM"), h("p", { cls: `small plat-state ${kind}` }, word))),
    h("p", { cls: "small muted" }, `When someone comments ${(d.keywords || [])[0] || "the keyword"} on a post from the last 7 days, ${b.name} sends them this message once, and replies "Sent you a DM" under the comment. Reels then end with "Comment ${(d.keywords || [])[0] || "…"}" instead of "Follow". {name} becomes their @username.`),
    d.note && d.on ? h("p", { cls: "small", style: "color:#7D1C26" }, d.note) : null,
    b.id === "legalgroup" ? h("p", { cls: "tiny", style: "color:#6E4300" }, "Advocates in India may not solicit work (Bar Council of India rules), so this message only points to free explainers. Keep it informational.") : null,
    h("div", { cls: "form-grid" }, h("label", { cls: "fld" }, h("span", {}, "Keyword"), kw),
      h("label", { cls: "fld", style: "display:flex;gap:8px;align-items:center;margin-top:26px" }, on, h("span", {}, "On")),
      h("label", { cls: "fld", style: "display:flex;gap:8px;align-items:center;margin-top:26px" }, pub, h("span", {}, "Also reply \"Sent you a DM\" publicly"))),
    h("label", { cls: "fld" }, h("span", {}, "Message (max 900 characters)"), msgIn),
    h("div", { cls: "svc-btns" }, h("button", { cls: "btn primary", onclick: async () => {
      msg.textContent = "Saving…";
      try { await rpc("set_dm", { p_brand: b.id, p_on: on.checked, p_keyword: kw.value.trim(), p_message: msgIn.value.trim(), p_public_reply: pub.checked });
        await loadSettings(); toast(on.checked ? `Comment-to-DM on for ${b.name}. Instagram is checked within 2 minutes.` : "Comment-to-DM saved (off)."); refresh(); }
      catch (e) { msg.textContent = errText(e); } } }, "Save"), msg),
    h("p", { cls: "small", style: "margin-top:8px" }, h("b", {}, `${sent.length} sent in the last 7 days`),
      log.length ? h("span", { cls: "muted" }, ` · latest: ${log.slice(0, 4).map((r) => `@${r.username} (${r.state}, ${ago(r.created_at)})`).join(", ")}`) : null));
}
function webCard(b, refresh) {
  const web = safeUrl(b.website); const v = stOf("website_status", b.id); const s_ = web ? v.state : "not_connected";
  const kind = !web ? "no" : s_ === "connected" ? "ok" : s_ === "error" ? "warn" : "wait";
  const word = !web ? "Not set" : s_ === "connected" ? `Online${v.ms ? ` · answered in ${(v.ms / 1000).toFixed(1)} s` : ""}` : s_ === "error" ? "Not reachable" : `${host(web)} · not checked yet`;
  return h("article", { cls: `svc conn ${kind}` }, h("div", { cls: "svc-head" }, h("span", { cls: "plat-tile svc-ico" }, h("img", { src: PLAT_IMG.web, alt: "" }),
      h("span", { cls: `plat-badge ${kind}`, "aria-hidden": "true" }, kind === "ok" ? "✓" : kind === "no" ? "✕" : kind === "wait" ? "…" : "!")),
    h("div", {}, h("h3", {}, "Website"), h("p", { cls: `small plat-state ${kind}` }, word))),
    h("p", { cls: "small muted" }, v.note && web ? v.note : web ? `${host(web)} · checked ${v.checked_at ? ago(v.checked_at) : "soon"}. Studied by Account Intelligence; never shown as a link inside reels.` : "Add the website in the profile above."),
    web ? h("div", { cls: "svc-btns" }, recheckBtn("web", b, refresh), h("a", { cls: "btn quiet", href: web, target: "_blank", rel: "noopener noreferrer" }, icon("ext", 16), "Open")) : null);
}
function stepsDlg(label, title, sub, steps, fields, msg, go_) {
  const d = h("dialog", { "aria-label": label, cls: "connect" }, h("div", { style: "padding:20px 20px 4px" },
    h("h2", { style: "font-size:22px" }, title), h("p", { cls: "small muted", style: "margin:6px 0 12px" }, sub),
    h("ol", { cls: "steps" }, steps.map(([t, x]) => h("li", {}, h("b", {}, t), h("span", {}, x)))), fields, msg),
    h("div", { cls: "dlg-foot" }, h("button", { cls: "btn quiet", onclick: () => d.close() }, "Close"), go_));
  d.addEventListener("close", () => d.remove()); document.body.append(d); d.showModal(); return d;
}
const secretInput = (ph, label) => h("input", { type: "password", autocomplete: "off", spellcheck: "false", placeholder: ph, "aria-label": label || ph, style: "margin-top:8px" });
const statusMsg = () => h("p", { cls: "small", role: "status", style: "min-height:20px;margin-top:8px" });
function igDialog(b, refresh) {
  const tok = secretInput("Paste the access token", "Instagram access token"); const msg = statusMsg();
  const SK = bkey("instagram_status", b.id);
  const save = h("button", { cls: "btn primary", onclick: async () => {
    if (!tok.value.trim()) { tok.focus(); return; }
    save.disabled = true; msg.textContent = "Saving…";
    try {
      await rpc("set_instagram_token", { p_token: tok.value.trim(), p_brand: b.id }); tok.value = ""; msg.textContent = "Saved. Checking it with Instagram (in the cloud, about a minute)…";
      for (let i = 0; i < 40; i++) { await sleep(3000); await loadSettings(); const st = S.settings[SK] || {};
        if (st.state === "connected") { msg.textContent = `Connected as @${st.username}.`; toast(`${b.name}: Instagram connected as @${st.username}.`); await sleep(900); d.close(); refresh(); return; }
        if (["expired", "error", "duplicate"].includes(st.state)) { msg.textContent = st.note || "Instagram did not accept the token."; save.disabled = false; refresh(); return; } }
      msg.textContent = "Still checking; this page updates by itself."; save.disabled = false;
    } catch (e) { msg.textContent = errText(e); save.disabled = false; }
  } }, "Save and check");
  const d = stepsDlg("Connect Instagram", `Connect ${b.name}'s Instagram`, "Free, official Instagram API. One time, about 5 minutes. Reels of this account then post only here.", [
    ["Open developers.facebook.com/apps", "Your Reel Studio app (or Create App → Other → Business)."],
    ["Add the account", `Instagram → API setup with Instagram login → Generate access tokens → Add account → log in to ${b.name}'s Instagram.`],
    ["Copy the token", "Click Generate token next to that account and paste it below. It stays on the server; the site never shows it again."]], tok, msg, save);
  tok.focus();
}
function fbDialog(b, refresh) {
  const meta = S.settings?.meta_app || {}; const saved = !!meta.app_id && meta.state !== "not_set" && meta.state !== "bad";
  const inp = (ph) => h("input", { type: "text", autocomplete: "off", spellcheck: "false", placeholder: ph, "aria-label": ph, style: "margin-top:8px" });
  const appId = inp("App ID (numbers)"), secret = secretInput("App Secret"), tok = secretInput("Access token from Graph API Explorer");
  const pick = h("div"); let pageId = null; const msg = statusMsg();
  const go_ = h("button", { cls: "btn primary", onclick: async () => {
    if (!tok.value.trim() || (!saved && (!appId.value.trim() || !secret.value.trim()))) { msg.textContent = saved ? "Paste the access token." : "Fill in all three boxes."; return; }
    go_.disabled = true; msg.textContent = "Connecting…";
    const body = { user_token: tok.value.trim(), brand: b.id, page_id: pageId, ...(saved ? {} : { app_id: appId.value.trim(), app_secret: secret.value.trim() }) };
    const { data, error } = await sb.functions.invoke("fb-auth", { body });
    let err = data?.error, pages = data?.pages; if (error && !err) { try { const j = await error.context?.json(); err = j?.error; pages = j?.pages; } catch { /* */ } err ||= error.message; }
    if (pages?.length) {          // more than one Page: choose which one belongs to this account
      msg.textContent = err; go_.disabled = false;
      pick.replaceChildren(h("p", { cls: "small", style: "margin:10px 0 4px;font-weight:600" }, `Which Page is ${b.name}?`),
        ...pages.map((p) => h("label", { cls: "small", style: "display:flex;gap:8px;align-items:center;margin:4px 0" },
          h("input", { type: "radio", name: "fbpage", value: p.id, onchange: () => { pageId = p.id; } }), p.name)));
      return;
    }
    if (err) { msg.textContent = err; go_.disabled = false; return; }
    secret.value = ""; tok.value = ""; toast(`${b.name}: Facebook connected to ${data.page_name}.`); await loadSettings().catch(() => {}); d.close(); refresh();
  } }, "Connect");
  const steps = [
    ["Page use case", "developers.facebook.com → your Reel Studio app → Use cases → Manage everything on your Page."],
    ...(saved ? [] : [["App ID and App Secret", "App settings → Basic (not the Instagram app ID). Save them once in Studio Settings to skip this next time."]]),
    ["Get a token", `Tools → Graph API Explorer → Get User Access Token → tick pages_show_list, pages_read_engagement, pages_manage_posts → Generate → choose the ${b.name} Page → copy.`],
    ["Connect", "Only the Page's own token is kept, on the server; it does not expire."]];
  const d = stepsDlg("Connect Facebook", `Connect ${b.name}'s Facebook Page`, saved ? `Uses the Meta app saved in Studio Settings (App ID ${meta.app_id}).` : "Free, official Facebook API.",
    steps, [saved ? null : appId, saved ? null : secret, tok, pick], msg, go_);
  (saved ? tok : appId).focus();
}
function liDialog(b, refresh) {
  const app = S.settings?.linkedin_app || {};
  const go_ = h("button", { cls: "btn primary", disabled: !app.client_id, onclick: () => liSignIn(app.client_id, b.id) }, "Sign in with LinkedIn");
  const vanity = (brandOf(b.id).links?.linkedin || "");
  stepsDlg("Connect LinkedIn", `Connect ${b.name}'s LinkedIn page`, app.client_id ? `Uses the LinkedIn app saved in Studio Settings (Client ID ${app.client_id}).` : "First save the LinkedIn app in Studio Settings.", [
    ["LinkedIn app", app.client_id ? "Saved in Studio Settings." : "Studio Settings → Developer apps → LinkedIn: paste the app's Client ID and Client Secret."],
    ["Sign in", `Sign in as an admin of ${b.name}'s Company Page. ${vanity ? "The page in this account's profile is picked: " + vanity : "Tip: add the page link in this account's profile so the right page is picked."}`]],
    null, statusMsg(), go_);
}
async function finishLinkedIn() {     // back from LinkedIn's sign-in page: ?code=…&state=…
  const q = new URLSearchParams(location.search); if (!q.has("code") && !q.has("error")) return;
  let expected = null, b = "weone"; try { expected = sessionStorage.getItem("li_state"); b = sessionStorage.getItem("li_brand") || "weone"; sessionStorage.removeItem("li_state"); sessionStorage.removeItem("li_brand"); } catch { /* */ }
  setBrand(b); history.replaceState(null, "", location.pathname + "#/account");
  if (q.has("error")) { toast(`LinkedIn: ${q.get("error_description") || q.get("error")}`, { err: true, ms: 9000 }); render(); return; }
  if (!expected || q.get("state") !== expected) { toast("LinkedIn sign-in could not be verified. Start Connect LinkedIn again.", { err: true, ms: 9000 }); render(); return; }
  toast("Connecting the LinkedIn page…", { ms: 4000 });
  const { data, error } = await sb.functions.invoke("linkedin-auth", { body: { code: q.get("code"), redirect_uri: LI_REDIRECT, brand: b } });
  let msg = data?.error; if (error && !msg) { try { msg = (await error.context?.json())?.error; } catch { /* */ } msg ||= error.message; }
  if (msg) toast(`LinkedIn: ${msg}`, { err: true, ms: 12000 });
  else toast(`${brandOf(b).name}: LinkedIn connected to ${data.org_name}.`, { ms: 8000 });
  await loadSettings().catch(() => {}); go("account");
}
// Library: small status tiles; tapping one opens the account's Settings
function platformBar() {
  return h("section", { cls: "plats", "aria-label": "Connected accounts" }, ["ig", "fb", "li"].map((k) => {
    const st = stOf(PST[k]); const state = st.state || "not_connected";
    const kind = state === "connected" ? "ok" : state === "not_connected" ? "no" : state === "checking" ? "wait" : "warn";
    const word = { ok: "Connected", no: "Not connected", wait: "Checking…", warn: "Needs attention" }[kind];
    return h("button", { cls: `plat ${k}`, "aria-label": `${PLAT[k]}: ${word}. Open Settings.`, onclick: () => go("account") },
      h("span", { cls: "plat-tile" }, h("img", { src: PLAT_IMG[k], alt: "", width: 56, height: 56 }),
        h("span", { cls: `plat-badge ${kind}`, "aria-hidden": "true" }, kind === "ok" ? "✓" : kind === "no" ? "✕" : kind === "wait" ? "…" : "!")),
      h("span", { cls: "plat-name" }, PLAT[k]), h("span", { cls: `plat-state ${kind}` }, word));
  }));
}

// ---------------------------------------------------------------- Account settings: profile, logo, its own connections
async function viewAccount() {
  const body = h("div", {}, h("div", { cls: "skeleton", style: "height:300px" }));
  shell(h("div", {}, h("div", { cls: "page-head" }, h("div", {}, h("h1", {}, "Settings"),
    h("p", {}, `${brandOf().name} only: its profile, logo and its own Instagram, Facebook and LinkedIn. Everything here works even when the Mac and Claude are offline.`))), body));
  let sig = "";
  const paint = async (force) => {
    if (!body.isConnected) return;
    await Promise.all([loadSettings().catch(() => {}), loadBrands().catch(() => {}), loadOwner()]);
    const since = new Date(Date.now() - 7 * 86400e3).toISOString();
    const dmLog = (await sb.from("dm_log").select("username,state,created_at").eq("brand", S.brand).gte("created_at", since).order("created_at", { ascending: false }).limit(200).then((r) => r.data, () => [])) || [];
    const b = brandOf(); const nowSig = JSON.stringify([b, ["ig", "fb", "li"].map((k) => stOf(PST[k])), stOf("website_status"), S.settings?.[bkey("dm")], dmLog.length, S.settings?.linkedin_app, S.settings?.meta_app]);
    if (!force && nowSig === sig) return; sig = nowSig;
    if (body.contains(document.activeElement) && /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) return;     // never wipe a half-typed field
    const L = b.links || {};
    const f = (name, label, val, ph, type = "text") => h("label", { cls: "fld" }, h("span", {}, label), h("input", { name, type, value: val || "", placeholder: ph, maxlength: "300", autocomplete: "off" }));
    const msg = h("p", { cls: "small", role: "status" });
    const form = h("form", { cls: "acct-form", onsubmit: async (e) => {
      e.preventDefault(); const v = Object.fromEntries(new FormData(form)); const btn = form.querySelector("button[type=submit]"); btn.disabled = true; msg.textContent = "Saving…";
      let web = String(v.website || "").trim(); if (web && !/^https?:\/\//i.test(web)) web = "https://" + web;
      try { await rpc("update_brand", { p_brand: b.id, p_patch: { name: v.name, tagline: v.tagline, website: web,
          links: { instagram: v.instagram || "", facebook: v.facebook || "", linkedin: v.linkedin || "", youtube: v.youtube || "" } } });
        msg.textContent = "Saved."; toast(`${v.name} profile saved.`); await loadBrands(); sig = ""; paint(true); }
      catch (err) { msg.textContent = errText(err); btn.disabled = false; }
    } },
      h("div", { cls: "form-grid" }, f("name", "Business name", b.name), f("tagline", "Tagline", b.tagline, "What it does, in one line"), f("website", "Website", b.website, "https://…"),
        f("instagram", "Instagram page", L.instagram, "https://www.instagram.com/…"), f("facebook", "Facebook page", L.facebook, "https://www.facebook.com/…"),
        f("linkedin", "LinkedIn page", L.linkedin, "https://www.linkedin.com/company/…"), f("youtube", "YouTube (optional)", L.youtube, "https://www.youtube.com/@…")),
      h("div", { cls: "svc-btns" }, h("button", { cls: "btn primary", type: "submit" }, "Save profile"), msg));
    const logo = h("section", { cls: "panel" }, h("h3", {}, "Assets"),
      h("div", { cls: "asset-row", style: "margin-top:12px" }, h("span", { cls: "blogo asset-prev" }, h("img", { src: logoSrc(b), alt: `${b.name} logo`, "data-logo": b.id })),
        h("div", {}, h("b", {}, "Logo"), h("p", { cls: "small muted" }, b.logo_updated_at ? `Updated ${tzClock(b.logo_updated_at)}. Used on every reel of this account (watermark and end card).` : "Built-in logo. Used on every reel of this account (watermark and end card)."),
          h("button", { cls: "btn quiet", onclick: () => assetsDialog(b, () => paint(true)) }, "Change logo"))));
    const web = safeUrl(b.website);
    body.replaceChildren(
      h("section", { cls: "panel" }, h("div", { cls: "ai-head" }, brandLogo(b, 48), h("div", {}, h("h3", {}, "Profile"),
        h("p", { cls: "small muted" }, "Account Intelligence reads these pages when it studies the account; LinkedIn uses the page link to pick the right Company Page."))), form),
      logo,
      h("section", { cls: "panel" }, h("h3", {}, "Connections"),
        h("p", { cls: "small muted", style: "margin:4px 0 14px" }, `Reels of ${b.name} post only to the pages connected here. A page connected to another account can't be connected here too.`),
        h("div", { cls: "svc-grid" }, ["ig", "fb", "li"].map((k) => connCard(k, b, () => paint(true))), webCard(b, () => paint(true)))),
      h("section", { cls: "panel" }, h("h3", {}, "Call to action"),
        h("p", { cls: "small muted", style: "margin:4px 0 14px" }, "Turns comments into conversations. Works in the cloud, also while the Mac is off."),
        h("div", { cls: "svc-grid" }, dmCard(b, dmLog, () => paint(true)))));
  };
  await paint(true).catch((e) => body.replaceChildren(h("div", { cls: "panel empty" }, h("p", {}, errText(e)))));
  S.timers.push(setInterval(() => paint(false).catch(() => {}), 8000));
}

// ---------------------------------------------------------------- Studio settings: keys and connections shared by every account
async function loadOwner() { if (S.owner == null) { try { S.owner = !!(await rpc("is_owner", {})); } catch { S.owner = false; } } return S.owner; }
const SB_REF = (() => { try { return new URL(SUPABASE_URL).hostname.split(".")[0]; } catch { return ""; } })();
const VERCEL_PROJECT = "https://vercel.com/weone404s-projects/weone-reelstudio";
function keyCard({ id, name, img, wide, get, desc, help }, refresh) {
  const st = (S.settings?.studio_keys || {})[id] || { state: "not_set" }; const used = S.worker?.info?.keys?.[id];
  const kind = st.state === "ok" ? "ok" : st.state === "bad" ? "warn" : st.state === "checking" ? "wait" : "no";
  const word = { ok: "Working", warn: "Not accepted", wait: "Checking…", no: "Not saved here" }[kind];
  const inp = secretInput(st.hint ? `Paste a new key to replace ${st.hint}` : `Paste the ${name} key`, `${name} key`); const msg = statusMsg();
  const save = async (value) => {
    msg.textContent = "Saving…";
    try { await rpc("set_studio_key", { p_name: id, p_value: value }); inp.value = ""; await loadSettings(); toast(value ? `${name} key saved. Checking it now; the Mac uses it from its next search.` : `${name} key removed.`); refresh(); }
    catch (e) { msg.textContent = errText(e); }
  };
  return h("article", { cls: `svc ${kind}` },
    h("div", { cls: "svc-head" }, h("span", { cls: "svc-logo" + (wide ? " wide" : "") }, h("img", { src: img, alt: name })), h("div", {}, wide ? h("h3", { cls: "sr" }, name) : h("h3", {}, name),
      h("p", { cls: "small" }, h("i", { cls: "lamp " + (kind === "warn" ? "bad" : kind === "no" ? "" : kind) }), word, st.hint ? ` · key ${st.hint}` : ""))),
    h("p", { cls: "small muted" }, desc),
    st.note ? h("p", { cls: "small", style: "color:#7D1C26" }, st.note) : null,
    h("p", { cls: "tiny faint" }, [st.updated_at && st.state !== "not_set" ? `Changed ${ago(st.updated_at)}` : null, st.checked_at ? `checked ${ago(st.checked_at)}` : null,
      used === "cloud" ? "the Mac uses this key" : used === "mac" ? "the Mac uses its own .env key" : used === null && S.worker ? "the Mac has no key" : null].filter(Boolean).join(" · ")),
    S.owner ? [inp, h("div", { cls: "svc-btns" }, h("button", { cls: "btn primary", onclick: () => inp.value.trim() ? save(inp.value.trim()) : inp.focus() }, st.hint ? "Replace key" : "Save key"),
      st.hint ? h("button", { cls: "btn quiet danger", onclick: (e) => armed(e, "Tap again to remove", () => save("")) }, "Remove") : null,
      h("a", { cls: "btn quiet", href: get, target: "_blank", rel: "noopener noreferrer" }, icon("ext", 16), help)), msg] : null);
}
function metaCard(refresh) {
  const m = S.settings?.meta_app || { state: "not_set" };
  const kind = m.state === "ok" ? "ok" : m.state === "bad" ? "warn" : m.state === "checking" ? "wait" : "no";
  const id = h("input", { type: "text", autocomplete: "off", placeholder: "App ID (numbers)", value: m.app_id || "", "aria-label": "Meta App ID", style: "margin-top:8px" });
  const sec = secretInput(m.hint ? `App Secret (saved ${m.hint})` : "App Secret"); const msg = statusMsg();
  return h("article", { cls: `svc ${kind}` },
    h("div", { cls: "svc-head" }, h("span", { cls: "svc-logo" }, h("img", { src: SVC_IMG.meta, alt: "" })), h("div", {}, h("h3", {}, "Meta app"),
      h("p", { cls: "small" }, h("i", { cls: "lamp " + (kind === "warn" ? "bad" : kind === "no" ? "" : kind) }), { ok: `Working${m.name ? ": " + m.name : ""}`, warn: "Not accepted", wait: "Checking…", no: "Not saved" }[kind]))),
    h("p", { cls: "small muted" }, "Used by Connect Facebook of every account (the App Secret turns a short token into a Page token that does not expire). Instagram tokens are made in the same app."),
    m.note ? h("p", { cls: "small", style: "color:#7D1C26" }, m.note) : null,
    S.owner ? [id, sec, h("div", { cls: "svc-btns" }, h("button", { cls: "btn primary", onclick: async () => {
      if (!id.value.trim() || !sec.value.trim()) { msg.textContent = "Fill in App ID and App Secret."; return; }
      msg.textContent = "Saving…";
      try { await rpc("set_meta_app", { p_app_id: id.value.trim(), p_app_secret: sec.value.trim() }); sec.value = ""; await loadSettings(); toast("Meta app saved. Checking it now…"); refresh(); }
      catch (e) { msg.textContent = errText(e); } } }, m.app_id ? "Replace" : "Save"),
      h("a", { cls: "btn quiet", href: "https://developers.facebook.com/apps/", target: "_blank", rel: "noopener noreferrer" }, icon("ext", 16), "Meta apps")), msg] : null);
}
// Cloudflare R2: where reels wait to be posted (10 GB free, no download fees). Until it is checked, Supabase is used.
function r2Card(refresh) {
  const c = S.settings?.r2 || { state: "not_set" };
  const kind = c.state === "ok" ? "ok" : c.state === "bad" ? "warn" : c.state === "checking" ? "wait" : "no";
  const f = (ph, label, val) => h("input", { type: "text", autocomplete: "off", spellcheck: "false", placeholder: ph, value: val || "", "aria-label": label, style: "margin-top:8px" });
  const acc = f("Account ID (32 characters)", "Cloudflare Account ID", c.account_id), bucket = f("Bucket name, e.g. reel-studio", "R2 bucket name", c.bucket);
  const kid = secretInput(c.hint ? "Access Key ID (saved)" : "Access Key ID", "R2 Access Key ID"), sec = secretInput(c.hint ? `Secret Access Key (saved ${c.hint})` : "Secret Access Key", "R2 Secret Access Key");
  const msg = statusMsg();
  return h("article", { cls: `svc ${kind}` },
    h("div", { cls: "svc-head" }, h("span", { cls: "svc-logo" }, h("img", { src: SVC_IMG.cloudflare, alt: "" })), h("div", {}, h("h3", {}, "Cloudflare R2 (reel storage)"),
      h("p", { cls: "small" }, h("i", { cls: "lamp " + (kind === "warn" ? "bad" : kind === "no" ? "" : kind) }), { ok: `Working: bucket ${c.bucket}`, warn: "Not accepted", wait: "Checking…", no: "Not set up: reels use Supabase" }[kind]))),
    h("p", { cls: "small muted" }, "Each reel keeps one posting copy in the cloud, removed 3 days after it is posted (7 days if never reviewed). The Mac deletes its work files and masters too. Thumbnails, results and scripts stay."),
    h("ol", { cls: "small muted", style: "margin:6px 0 0 18px" }, h("li", {}, "Cloudflare dashboard → R2 → Create bucket (any name)."), h("li", {}, "R2 → Manage API tokens → Create token: Object Read & Write, this bucket only."),
      h("li", {}, "Paste the Account ID, bucket name, Access Key ID and Secret here."), h("li", {}, "Optional safety net: bucket → Settings → Object lifecycle → delete after 7 days.")),
    c.note ? h("p", { cls: "small", style: "color:#7D1C26" }, c.note) : null,
    S.owner ? [acc, bucket, kid, sec, h("div", { cls: "svc-btns" }, h("button", { cls: "btn primary", onclick: async () => {
      if (![acc, bucket, kid, sec].every((x) => x.value.trim())) { msg.textContent = "Fill in all four fields."; return; }
      msg.textContent = "Saving…";
      try { await rpc("set_r2", { p_account: acc.value.trim(), p_bucket: bucket.value.trim(), p_key_id: kid.value.trim(), p_secret: sec.value.trim() });
        kid.value = ""; sec.value = ""; await loadSettings(); toast("R2 saved. The cloud checks it within a minute; new reels go there once it works."); refresh(); }
      catch (e) { msg.textContent = errText(e); } } }, c.account_id ? "Replace" : "Save"),
      c.account_id ? h("button", { cls: "btn quiet danger", onclick: (e) => armed(e, "Tap again: new reels go back to Supabase", async () => {
        try { await rpc("set_r2", { p_account: "", p_bucket: "", p_key_id: "", p_secret: "" }); await loadSettings(); toast("R2 removed. New reels are stored in Supabase."); } catch (err) { toast(errText(err), { err: true }); } refresh(); }) }, "Remove") : null,
      h("a", { cls: "btn quiet", href: "https://dash.cloudflare.com/?to=/:account/r2/overview", target: "_blank", rel: "noopener noreferrer" }, icon("ext", 16), "Cloudflare R2")), msg]
      : h("p", { cls: "tiny faint" }, "Only the studio owner can change storage."));
}
function liAppCard(refresh) {
  const app = S.settings?.linkedin_app || {};
  const cid = h("input", { type: "text", autocomplete: "off", spellcheck: "false", placeholder: "Client ID", value: app.client_id || "", "aria-label": "LinkedIn Client ID", style: "margin-top:8px" });
  const sec = secretInput(app.hint ? `Client Secret (saved ${app.hint})` : "Client Secret"); const msg = statusMsg();
  return h("article", { cls: `svc ${app.client_id ? "ok" : "no"}` },
    h("div", { cls: "svc-head" }, h("span", { cls: "svc-logo" }, h("img", { src: PLAT_IMG.li, alt: "" })), h("div", {}, h("h3", {}, "LinkedIn app"),
      h("p", { cls: "small" }, h("i", { cls: "lamp " + (app.client_id ? "ok" : "") }), app.client_id ? `Saved · Client ID ${app.client_id}` : "Not saved"))),
    h("p", { cls: "small muted" }, "Used by Connect LinkedIn of every account. Needs the Community Management API. Authorized redirect URL: ", h("code", {}, LI_REDIRECT)),
    S.owner ? [cid, sec, h("div", { cls: "svc-btns" }, h("button", { cls: "btn primary", onclick: async () => {
      if (!cid.value.trim() || !sec.value.trim()) { msg.textContent = "Fill in Client ID and Client Secret."; return; }
      msg.textContent = "Saving…";
      try { await rpc("set_linkedin_app", { p_client_id: cid.value.trim(), p_client_secret: sec.value.trim() }); sec.value = ""; await loadSettings(); toast("LinkedIn app saved."); refresh(); }
      catch (e) { msg.textContent = errText(e); } } }, app.client_id ? "Replace" : "Save"),
      h("a", { cls: "btn quiet", href: "https://www.linkedin.com/developers/apps", target: "_blank", rel: "noopener noreferrer" }, icon("ext", 16), "LinkedIn apps")), msg] : null);
}
const VOICE_OPTS = { hi: [["hi", "Chatterbox V3 Hindi pack (best for Hinglish)"], ["v2", "Chatterbox V2"], ["v3", "Chatterbox V3"]],
  en: [["hi", "Hindi pack: Indian English accent"], ["v2", "Chatterbox V2"], ["v3", "Chatterbox V3"]] };
const STYLE_OPTS = [["natural", "Natural: balanced, steady"], ["expressive", "Expressive: more emotion, calmer pace"]];
function voiceCard(refresh) {
  const ws = S.settings?.worker_settings || {}; const msg = statusMsg();
  const sel = (k, val) => h("select", { "aria-label": k === "hi" ? "Hinglish voice model" : "English voice model", style: "width:100%;margin-top:4px" },
    VOICE_OPTS[k].map(([v, label]) => h("option", { value: v, selected: v === val ? true : null }, label)));
  const cur = S.worker?.info?.voices || {};
  const hi = sel("hi", ws.voice_hi || cur.hi || "v2"), en = sel("en", ws.voice_en || cur.en || "v2");
  const sty = (k, val) => h("select", { "aria-label": k === "hi" ? "Hinglish delivery" : "English delivery", style: "width:100%;margin-top:4px" },
    STYLE_OPTS.map(([v, label]) => h("option", { value: v, selected: v === val ? true : null }, label)));
  const shi = sty("hi", ws.style_hi || "natural"), sen = sty("en", ws.style_en || "natural");
  return h("article", { cls: "svc ok" },
    h("div", { cls: "svc-head" }, h("span", { cls: "svc-logo" }, icon("mic", 24)),
      h("div", {}, h("h3", {}, "Voice model"), h("p", { cls: "small" }, h("i", { cls: "lamp ok" }), ws.voice_at ? `Changed ${ago(ws.voice_at)}` : "Studio default",
        cur.ready ? ` · on the Mac: ${cur.ready.join(", ")}` : ""))),
    h("label", { cls: "small", style: "font-weight:600;margin-top:6px" }, "Hinglish accounts (We One Aviation, Flying Star Aviator)"), hi, shi,
    h("label", { cls: "small", style: "font-weight:600;margin-top:8px" }, "English accounts (Book My Charter, Legal Group)"), en, sen,
    h("p", { cls: "tiny faint" }, "The Mac uses the new choice from the next reel's voice step. Every take is still checked word by word."),
    S.owner ? h("div", { cls: "svc-btns" }, h("button", { cls: "btn primary", onclick: async () => {
      try { await rpc("set_voice_models", { p_hi: hi.value, p_en: en.value }); await rpc("set_voice_style", { p_hi: shi.value, p_en: sen.value });
        await loadSettings(); toast("Voice saved."); refresh(); }
      catch (e) { msg.textContent = errText(e); } } }, "Save"), msg) : null);
}
function infoCard({ img, name, state, word, lines, links, mono }) {
  return h("article", { cls: `svc ${state}` },
    h("div", { cls: "svc-head" }, h("span", { cls: "svc-logo" + (mono ? " mono" : "") }, h("img", { src: img, alt: "" })), h("div", {}, h("h3", {}, name),
      h("p", { cls: "small" }, h("i", { cls: "lamp " + (state === "warn" ? "bad" : state === "no" ? "" : state) }), word))),
    lines.filter(Boolean).map((l) => h("p", { cls: "small muted" }, l)),
    links?.length ? h("div", { cls: "svc-btns" }, links.map(([label, url]) => h("a", { cls: "btn quiet", href: url, target: "_blank", rel: "noopener noreferrer" }, icon("ext", 16), label))) : null);
}
async function viewSettings() {
  const body = h("div", {}, h("div", { cls: "skeleton", style: "height:300px" }));
  shell(h("div", {}, h("div", { cls: "page-head" }, h("div", {}, h("h1", {}, "Studio settings"),
    h("p", {}, "Keys and connections shared by every account. Saved in the cloud, so you can change them any time, even when the Mac and Claude are offline. Keys can be replaced but never shown again; only their last 4 characters are visible."))), body));
  let sig = "";
  const paint = async (force) => {
    if (!body.isConnected) return;
    await Promise.all([loadSettings().catch(() => {}), loadWorker().catch(() => {}), loadOwner()]);
    const nowSig = JSON.stringify([S.settings?.studio_keys, S.settings?.meta_app, S.settings?.r2, S.settings?.linkedin_app, S.settings?.post_schedule, S.settings?.worker_settings, S.worker?.info?.keys, macAlive(), S.worker?.claude_ok, S.editSlots]);
    if (!force && nowSig === sig) return; sig = nowSig;
    if (body.contains(document.activeElement) && /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
    const rr = () => paint(true); const w = S.worker; const i = w?.info || {};
    const sch = S.settings?.post_schedule || {}; const cap = S.settings?.worker_settings?.daily_cap || i.daily_cap || 3;
    const capIn = h("input", { type: "number", min: "1", max: "10", value: String(cap), style: "width:80px", "aria-label": "New reels per day" });
    const sect = (title, sub, ...cards) => h("section", { cls: "set-sec" }, h("h2", {}, title), sub ? h("p", { cls: "small muted" }, sub) : null, h("div", { cls: "svc-grid" }, cards));
    body.replaceChildren(
      !S.owner ? h("div", { cls: "banner info", style: "margin-bottom:16px" }, "Only the studio owner can change keys. You can see their status.") : "",
      sect("Stock footage", "Where the clips in every reel come from. Free keys; the Mac falls back to its own .env key if one is removed here.",
        keyCard({ id: "pexels", name: "Pexels", img: SVC_IMG.pexels, wide: true, get: "https://www.pexels.com/api/", help: "Get a key", desc: "Main source of video clips (required)." }, rr),
        keyCard({ id: "pixabay", name: "Pixabay", img: SVC_IMG.pixabay, wide: true, get: "https://pixabay.com/api/docs/", help: "Get a key", desc: "Second source of video clips (optional, more choice)." }, rr)),
      sect("Developer apps", "Saved once here, used when you connect any account's Facebook or LinkedIn (in that account's Settings).", metaCard(rr), liAppCard(rr)),
      sect("Storage", "Where finished reels wait until they are posted. Nothing is kept longer than needed.", r2Card(rr)),
      sect("Posting", null, h("article", { cls: "svc" }, h("div", { cls: "svc-head" }, h("span", { cls: "svc-logo" }, icon("route", 26)), h("div", {}, h("h3", {}, "Auto-post"),
          h("p", { cls: "small" }, sch.auto === false ? "Off: approved reels post only when you tap Post." : `On: ${sch.review_minutes ?? 5} min after a reel is ready it posts to that account's own connected pages, unless you act.`))),
        S.editSlots ? slotEditor(sch, rr) : h("div", { cls: "svc-btns" }, h("button", { cls: "btn quiet", onclick: () => { S.editSlots = { minutes: sch.review_minutes ?? 5, auto: sch.auto !== false }; rr(); } }, "Change")))),
      sect("Voice", "The voice model that reads every reel, for the Hinglish accounts and the English accounts. Free, open models running on the Mac; switch back any time if one sounds worse.", voiceCard(rr)),
      sect("Studio Mac & Claude", "The Mac makes the reels; Claude Code on it writes and checks them.",
        h("article", { cls: `svc ${macAlive() ? "ok" : "warn"}` }, h("div", { cls: "svc-head" }, h("span", { cls: "svc-logo mono" }, h("img", { src: SVC_IMG.apple, alt: "" })), h("div", {}, h("h3", {}, "Studio Mac"),
            h("p", { cls: "small" }, h("i", { cls: "lamp " + (macAlive() ? "ok" : "bad") }), macAlive() ? "Online" : `Offline${w ? " · last heard " + ago(w.last_seen) : ""}`))),
          h("p", { cls: "small muted" }, w ? `Version ${w.version} · ${i.reels_today ?? 0} new reels today · ${i.disk_free_gb ?? "?"} GB free` : "No Mac has connected yet."),
          S.owner ? h("div", { cls: "svc-btns", style: "align-items:center" }, h("span", { cls: "small" }, "New reels per day"), capIn,
            h("button", { cls: "btn quiet", onclick: async () => { try { await rpc("set_worker_settings", { p_daily_cap: Number(capIn.value) }); await loadSettings(); toast(`Limit set to ${capIn.value} new reels a day. The Mac picks it up within 2 minutes.`); rr(); } catch (e) { toast(errText(e), { err: true }); } } }, "Save")) : null,
          h("p", { cls: "tiny faint" }, "Each reel uses about 10-12 Claude requests on the Pro plan; Request changes always runs, even after the limit.")),
        infoCard({ img: SVC_IMG.claude, name: "Claude", state: !macAlive() ? "no" : w?.claude_ok ? "ok" : "warn",
          word: !macAlive() ? "Waits for the Mac" : w?.claude_ok ? "Signed in and ready" : `Not ready${w?.claude_note ? ": " + w.claude_note : ""}`,
          lines: ["Claude Code on the Mac, signed in with your Claude plan (no API key). To switch accounts, run claude on the Mac and log in again."], links: [["Usage", "https://claude.ai/settings/usage"]] })),
      sect("Platform", "The site and the Mac sign in with these keys, so they are changed at the source, never from inside the site (one wrong edit here would lock everything out). The buttons open the exact page.",
        infoCard({ img: SVC_IMG.supabase, name: "Supabase", state: S.net.ok ? "ok" : "warn", word: S.net.ok ? "Connected" : "Connection dropped",
          lines: [`Project ${SB_REF}: database, sign-in, storage, cloud posting (Edge Functions) and the schedules.`, "Keys: the Mac's service key lives in its .env; the site uses only the public key."],
          links: [["API keys", `https://supabase.com/dashboard/project/${SB_REF}/settings/api-keys`], ["Function secrets", `https://supabase.com/dashboard/project/${SB_REF}/functions/secrets`]] }),
        infoCard({ img: SVC_IMG.vercel, name: "Vercel", mono: true, state: "ok", word: `Live at ${location.hostname}`,
          lines: ["Hosts this site (reel-studio-hq.vercel.app and weone-reelstudio.vercel.app). It holds no secrets: the site only needs the public Supabase address and key."],
          links: [["Environment variables", `${VERCEL_PROJECT}/settings/environment-variables`], ["Deployments", `${VERCEL_PROJECT}/deployments`]] }),
        infoCard({ img: SVC_IMG.github, name: "GitHub", mono: true, state: "ok", word: "nikkk07/reel-studio",
          lines: ["The studio's code and account profiles. No keys are stored in it."], links: [["Repository", "https://github.com/nikkk07/reel-studio"]] })));
  };
  await paint(true).catch((e) => body.replaceChildren(h("div", { cls: "panel empty" }, h("p", {}, errText(e)))));
  S.timers.push(setInterval(() => paint(false).catch(() => {}), 6000));
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
    panel.replaceChildren(!isLive() ? setupCard() : ownPosting() ? platformBar()
      : h("div", { cls: "banner info" }, h("span", {}, `${brandOf().name} has no Instagram, Facebook or LinkedIn connected yet. Approved reels are kept here to download; nothing is posted to another account.`),
          h("button", { cls: "btn quiet", style: "margin-left:auto", onclick: () => go("account") }, "Connect in Settings")));
    if (!data?.length) { grid.replaceChildren(h("div", { cls: "panel empty", style: "grid-column:1/-1" }, h("h3", {}, "Nothing here yet"), h("p", {}, "Approved reels appear here, ready to download and post."), h("button", { cls: "btn primary", onclick: () => go("create") }, "Create a reel"))); return; }
    const cards = await Promise.all(data.map(async (j) => {
      const o = j.output || {}; if (!(j.id in thumbs)) thumbs[j.id] = await signed(o.thumb);
      return h("div", { cls: "libcard" }, h("button", { cls: "poster", onclick: () => openPlayer(j) }, h("div", { cls: "frame" }, thumbs[j.id] ? h("img", { src: thumbs[j.id], alt: "", loading: "lazy" }) : null, h("span", { cls: "play" }, icon("play", 18, true))),
        h("h3", {}, titleOf(j)), h("p", { cls: "tiny" }, `${dateShort(j.updated_at)}, ${o.duration ?? "?"} s`)),
        ["ig", "fb", "li"].filter((p) => o[p] || platSet(p) || (p === "ig" && S.brand === "weone")).map((p) => [postBadge(j, p), postActions(j, p, () => refresh(true))]),
        !ownPosting() && !o.ig ? h("p", { cls: "ig-note" }, "Kept here to download. Connect this account's own pages in Settings to post.") : null);
    }));
    grid.replaceChildren(...cards);
  };
  await refresh(true);
  S.timers.push(setInterval(() => refresh(false).catch(() => {}), 15000));
}
async function openPlayer(j) {
  const o = j.output || {}; const name = `${titleOf(j).replace(/[^\w\- ]+/g, "").trim().slice(0, 50) || "reel"}.mp4`;
  const [url, dl] = await Promise.all([videoUrl(o), videoUrl(o, name)]);
  const d = h("dialog", { "aria-label": titleOf(j) }, url ? h("video", { src: url, controls: true, autoplay: true, playsinline: true }) : h("p", { style: "padding:24px" }, keptNote(o) || "This video is no longer stored in the cloud."),
    h("div", { cls: "dlg-foot" }, h("b", { style: "font-family:var(--f-head)" }, titleOf(j)), h("div", { style: "display:flex;gap:8px" },
      dl ? h("button", { cls: "btn primary", onclick: () => downloadReel(o, name) }, icon("down"), "Download") : null, h("button", { cls: "btn", onclick: () => d.close() }, "Close"))));
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
