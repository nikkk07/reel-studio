// Reel Studio – Instagram poster (Supabase Edge Function, called every minute by pg_cron).
// Runs in the cloud, so approved reels post even while the Mac is off. Each call does short steps only
// (create container / check it / publish), keeping state in jobs.output.ig, so nothing waits inside one call.
//
// Safety: the container id is saved before publishing and a PUBLISHED container is recognised, so a reel is never
// posted twice; failures back off 10/30/60/120 min then show Failed + Retry; an expired token pauses posting.

import { SB, H, type Json, type Brand, now, iso, minsAgo, rest, setting, setSetting, secret, setSecret, bk, brands } from "./db.ts";
import { liTick } from "./linkedin.ts";
import { fbTick } from "./facebook.ts";
import { keysTick } from "./keys.ts";
import { checksTick } from "./checks.ts";
import { mediaUrl } from "./media.ts";
import { retentionTick } from "./retention.ts";
import { dmTick } from "./dm.ts";
const API = Deno.env.get("IG_API") ?? "https://graph.instagram.com/v26.0";
const REFRESH_URL = Deno.env.get("IG_REFRESH_URL") ?? "https://graph.instagram.com/refresh_access_token";
const MAX_ATTEMPTS = 5;
const BACKOFF_MIN = [10, 30, 60, 120];
const TRANSIENT = new Set([1, 2, 4, 9, 17, 32, 341, 368, 613, 2207001, 2207003, 2207020, 2207032, 2207053]);

class IGError extends Error {
  constructor(msg: string, public code?: number, public transient = true, public auth = false, public drop = false) { super(msg); }
}

const token = (brand: string) => secret(bk("instagram_token", brand));
async function cronKey(): Promise<string | null> { const r = await rest("GET", "secrets?select=value&key=eq.cron_key"); return r?.[0]?.value ?? null; }
// write jobs.output.ig only if nobody changed the state meanwhile (two overlapping calls can never both act)
async function setIg(job: Json, ig: Json, expect?: string) {
  const out = { ...(job.output ?? {}), ig };
  const rows = await rest("PATCH", `jobs?id=eq.${job.id}&status=eq.done${expect ? `&output->ig->>state=eq.${expect}` : ""}`, { output: out });
  if (rows?.length) job.output = out;
  return !!rows?.length;
}

// ---------------------------------------------------------------- Instagram API
async function ig(method: "GET" | "POST", url: string, tok: string, params: Json = {}) {
  const p = new URLSearchParams({ ...params, access_token: tok });
  let r: Response;
  try { r = method === "GET" ? await fetch(`${url}?${p}`) : await fetch(url, { method: "POST", body: p }); }
  catch (e) { throw new IGError(`network problem talking to Instagram (${(e as Error).name})`); }
  let body: Json = {}; try { body = await r.json(); } catch { /* */ }
  if (!r.ok || body.error) {
    const e = body.error ?? {}; const code = e.code; const sub = e.error_subcode;
    const msg = String(e.error_user_msg ?? e.message ?? `HTTP ${r.status}`).split(tok).join("***").slice(0, 300);
    const auth = code === 190 || r.status === 401 || (code === 10 && (r.status === 400 || r.status === 403));
    const transient = !auth && (r.status >= 500 || TRANSIENT.has(code) || TRANSIENT.has(sub) || !!e.is_transient);
    throw new IGError(msg, code, transient, auth);
  }
  return body;
}

// one Instagram account belongs to one studio account: a second account trying to use it is refused
async function ownedElsewhere(brand: string, uid: string): Promise<string | null> {
  const rows: Json[] = await rest("GET", "settings?select=key,value&key=like.instagram_status*");
  const other = rows.find((r) => r.key !== bk("instagram_status", brand) && r.value?.state === "connected" && String(r.value?.user_id ?? "") === uid);
  if (!other) return null;
  const id = other.key.includes("@") ? other.key.split("@")[1] : "weone";
  const b = (await rest("GET", `brands?select=name&id=eq.${encodeURIComponent(id)}`))?.[0];
  return b?.name ?? id;
}

async function connection(brand: string, force: boolean): Promise<[string | null, string | null]> {
  const SK = bk("instagram_status", brand), TK = bk("instagram_token", brand);
  let [tok, savedAt] = await token(brand); const st = await setting(SK);
  if (!tok) {
    if (((st.state ?? "not_connected") !== "not_connected" && st.state !== "duplicate") || st.recheck_at)
      await setSetting(SK, st.state === "duplicate" ? { ...st, recheck_at: undefined, checked_at: iso(now()) } : { state: "not_connected", checked_at: iso(now()) });
    return [null, null];
  }
  if (!force && st.state === "connected" && minsAgo(st.checked_at) < 360) return [tok, st.user_id];
  try {
    if (minsAgo(savedAt!) > 3 * 1440) {                     // keep the 60-day token alive (allowed after 24 h)
      const r = await ig("GET", REFRESH_URL, tok, { grant_type: "ig_refresh_token" });
      if (r.access_token) {
        tok = r.access_token;
        await rest("PATCH", `secrets?key=eq.${encodeURIComponent(TK)}`, { value: tok, updated_at: iso(now()) }, "return=minimal");
        st.expires_at = iso(new Date(Date.now() + Number(r.expires_in ?? 5184000) * 1000));
      }
    }
    const me = await ig("GET", `${API}/me`, tok!, { fields: "user_id,username,account_type" });
    const owner = await ownedElsewhere(brand, String(me.user_id ?? me.id));
    if (owner) {
      await setSecret(TK, null);
      await setSetting(SK, { state: "duplicate", username: me.username, checked_at: iso(now()),
        note: `@${me.username} is already connected to ${owner}. Each account needs its own Instagram; log in to this account's Instagram when you generate the token.` });
      return [null, null];
    }
    const v = { state: "connected", username: me.username, user_id: String(me.user_id ?? me.id), account_type: me.account_type, checked_at: iso(now()),
      expires_at: st.expires_at ?? iso(new Date(Date.parse(savedAt!) + 60 * 86400e3)) };
    await setSetting(SK, v); return [tok, v.user_id];
  } catch (e) {
    const err = e as IGError;
    const note = err.auth ? "Instagram connection expired or was removed. Connect Instagram again in this account's Settings."
      : `Instagram is not reachable right now: ${err.message}`;
    const keep: Json = {}; for (const k of ["username", "user_id", "expires_at"]) if (st[k]) keep[k] = st[k];
    await setSetting(SK, { ...keep, state: err.auth ? "expired" : "error", note, checked_at: iso(now()) });
    return [null, null];
  }
}

// ---------------------------------------------------------------- posting steps
function fallbackCaption(o: Json, b: Brand) {      // only if the Mac could not write one (it normally does when the reel is made)
  const facts = (o.claims ?? []).slice(0, 2).map((c: Json) => c.claim).filter(Boolean).join("\n");
  if (b.id === "weone") return `${o.title ?? "Aviation fact"} ✈️\n\n${facts}\n\nSend this to a friend who wants to be a pilot.\n\n#aviation #pilottraining #indianaviation #aviationfacts #weoneaviation`.slice(0, 2200);
  return `${o.title ?? b.name}\n\n${facts}\n\nFollow ${b.name}.\n\n#${b.name.toLowerCase().replace(/[^a-z0-9]/g, "")}`.slice(0, 2200);
}
async function findPublished(tok: string, uid: string, caption: string, since?: string): Promise<[string | null, string | null]> {
  const head = (caption ?? "").slice(0, 60); const after = since ? Date.parse(since) - 120e3 : 0;
  const r = await ig("GET", `${API}/${uid}/media`, tok, { fields: "id,caption,permalink,timestamp", limit: "10" });
  const m = (r.data ?? []).find((m: Json) => head && String(m.caption ?? "").startsWith(head)
    && !(after && Date.parse(String(m.timestamp ?? "").replace(/([+-]\d\d)(\d\d)$/, "$1:$2")) < after));
  return m ? [m.id, m.permalink ?? null] : [null, null];
}
async function publish(tok: string, uid: string, cid: string): Promise<[string, string | null]> {
  const r = await ig("POST", `${API}/${uid}/media_publish`, tok, { creation_id: cid });
  if (!r.id) throw new IGError("Instagram did not return the post id");
  let link: string | null = null;
  try { link = (await ig("GET", `${API}/${r.id}`, tok, { fields: "permalink" })).permalink ?? null; } catch { /* */ }
  return [r.id, link];
}

async function step(job: Json, tok: string, uid: string, b: Brand): Promise<string> {
  const o = job.output ?? {}; let g: Json = { ...(o.ig ?? {}) };
  if (g.state === "posting" && g.container_id) {
    const st = await ig("GET", `${API}/${g.container_id}`, tok, { fields: "status_code,status" });
    const code = st.status_code;
    if (code === "IN_PROGRESS") {
      if (minsAgo(g.container_at) > 20) throw new IGError("Instagram took longer than 20 minutes to process the video", undefined, true, false, true);
      return "processing";
    }
    if (code === "FINISHED" || code === "PUBLISHED") {
      const [mid, link] = code === "FINISHED" ? await publish(tok, uid, g.container_id) : await findPublished(tok, uid, o.ig_caption || g.caption, g.container_at);
      g = { ...g, state: "posted", media_id: mid, permalink: link, posted_at: iso(now()) }; delete g.error;
      await setIg(job, g); return "posted";
    }
    throw new IGError(`Instagram could not process the video (${st.status ?? code})`, undefined, true, false, true);
  }
  if (g.state === "posting" && !g.container_id && minsAgo(g.started_at) < 3) return "starting";
  // due: claim it, then create the media container
  const prev = g.state;
  if ((!o.ig_video || !o.ig_caption) && minsAgo(o.decided_at) < 15) return "waiting for the Mac to prepare the file";
  g = { ...g, state: "posting", started_at: iso(now()), attempts: Number(g.attempts ?? 0) + (prev === "posting" ? 0 : 1) }; delete g.error;
  if (!(await setIg(job, g, prev))) return "taken";
  if (g.container_id) return "re-checking the earlier upload";      // never create a second container for the same reel
  const caption = o.ig_caption || fallbackCaption(o, b);
  const params: Json = { media_type: "REELS", video_url: await mediaUrl(o, o.ig_video ?? o.video), caption, share_to_feed: "true" };
  if (o.thumb) params.cover_url = await mediaUrl(o, o.thumb);
  if (o.title) params.audio_name = `${String(o.title).slice(0, 50)} · ${b.name}`;
  const c = await ig("POST", `${API}/${uid}/media`, tok, params);
  if (!c.id) throw new IGError("Instagram did not accept the video");
  g = { ...g, container_id: c.id, container_at: iso(now()), caption: caption.slice(0, 80) };
  await setIg(job, g); return "container created";
}

async function fail(job: Json, e: IGError, brand: string) {
  const g: Json = { ...(job.output?.ig ?? {}) }; const msg = e.message.slice(0, 300); const att = Number(g.attempts ?? 1);
  if (e.drop) { delete g.container_id; delete g.container_at; }     // only a failed/expired container is discarded
  if (e.auth) { await connection(brand, true); Object.assign(g, { state: "scheduled", at: iso(new Date(Date.now() + 10 * 60e3)), attempts: Math.max(0, att - 1), error: "Waiting for Instagram to be connected again." }); }
  else if (att >= MAX_ATTEMPTS || (!e.transient && att >= 2)) Object.assign(g, { state: "failed", error: `Posting failed: ${msg}` });
  else Object.assign(g, { state: "scheduled", at: iso(new Date(Date.now() + BACKOFF_MIN[Math.min(BACKOFF_MIN.length - 1, att - 1)] * 60e3)), error: msg });
  await setIg(job, g);
}

// ---------------------------------------------------------------- one tick: every account with its own Instagram
async function tickBrand(b: Brand): Promise<Json> {
  const reels: Json[] = await rest("GET", `jobs?select=id,output&kind=eq.reel&brand=eq.${b.id}&status=eq.done&output->ig->>state=in.(queued,scheduled,posting)&order=updated_at.asc&limit=50`);
  const due = reels.filter((r) => r.output.ig.state === "posting" || r.output.ig.state === "queued" || Date.parse(r.output.ig.at ?? 0) <= Date.now());
  const st = await setting(bk("instagram_status", b.id));
  if (!due.length && !st.recheck_at && (st.state ?? "not_connected") === "not_connected") return { connected: false };
  const [tok, uid] = await connection(b.id, st.state === "checking" || !!st.recheck_at || due.length > 0);       // "Re-check" on the site forces a fresh check
  const log: Json = {};
  if (!tok) {
    for (const r of due) if (r.output.ig.state !== "posting" && !r.output.ig.error) await setIg(r, { ...r.output.ig, error: "Waiting for Instagram to be connected again." }, r.output.ig.state);
    return { connected: false, due: due.length };
  }
  for (const r of due.slice(0, 3)) {
    try { log[r.id] = await step(r, tok, uid!, b); }
    catch (e) { const err = e instanceof IGError ? e : new IGError(String((e as Error).message ?? e)); await fail(r, err, b.id); log[r.id] = `error: ${err.message}`; }
  }
  return { connected: true, due: due.length, log };
}
export async function tick(list: Brand[]): Promise<Json> {
  const res: Json = {};
  for (const b of list) { try { res[b.id] = await tickBrand(b); } catch (e) { res[b.id] = { error: String((e as Error).message ?? e) }; } }
  return res;
}

// ---------------------------------------------------------------- results of posted reels (feeds the autopilot's learning)
const FULL = "views,reach,likes,comments,shares,saved,total_interactions,ig_reels_avg_watch_time,reels_skip_rate";
const CORE = "views,reach,likes,comments,shares,saved";
function statsDue(o: Json): boolean {
  const age = minsAgo(o.ig?.posted_at) / 60, last = minsAgo(o.ig_stats?.at) / 60;     // hours
  return age > 0.5 && last > (age < 48 ? 3 : age < 168 ? 12 : 24);
}
async function statsBrand(b: Brand): Promise<Json> {
  const since = iso(new Date(Date.now() - 30 * 86400e3));
  const reels: Json[] = await rest("GET", `jobs?select=id,output&kind=eq.reel&brand=eq.${b.id}&status=eq.done&output->ig->>state=eq.posted&output->ig->>posted_at=gte.${since}&order=updated_at.asc&limit=60`);
  const due = reels.filter((r) => r.output?.ig?.media_id && statsDue(r.output)).slice(0, 4);
  if (!due.length) return { due: 0 };
  const [tok] = await connection(b.id, false);
  if (!tok) return { connected: false };
  const cap = await setting(bk("instagram_insights", b.id));
  let full = !(cap.ok === false && minsAgo(cap.checked_at) < 720) && cap.metrics !== "core";
  let insights = !(cap.ok === false && minsAgo(cap.checked_at) < 720);
  let comments = !(cap.comments === false && minsAgo(cap.checked_at) < 720);
  const done: Json = {};
  for (const r of due) {
    const mid = r.output.ig.media_id; const s: Json = { at: iso(now()), hours_since_post: Math.round(minsAgo(r.output.ig.posted_at) / 6) / 10 };
    try {
      if (insights) {
        try {
          let j: Json;
          try { j = await ig("GET", `${API}/${mid}/insights`, tok, { metric: full ? FULL : CORE }); }
          catch (e) {
            if ((e as IGError).auth || !full) throw e;
            full = false; await setSetting(bk("instagram_insights", b.id), { ok: true, metrics: "core", checked_at: iso(now()) });
            j = await ig("GET", `${API}/${mid}/insights`, tok, { metric: CORE });
          }
          for (const m of j.data ?? []) {
            const v = m.values?.[0]?.value ?? m.total_value?.value;
            if (typeof v === "number") s[m.name === "ig_reels_avg_watch_time" ? "avg_watch_s" : m.name] = m.name === "ig_reels_avg_watch_time" ? Math.round(v / 100) / 10 : v;
          }
          s.source = "insights";
          if (cap.ok !== true) await setSetting(bk("instagram_insights", b.id), { ok: true, metrics: full ? "full" : "core", checked_at: iso(now()) });
        } catch (e) {
          if (!(e as IGError).auth && (e as IGError).code !== 10 && (e as IGError).code !== 200) throw e;
          insights = false; s.no_perm = true;   // probably no instagram_business_manage_insights: use public counts, never touch the connection
        }
      }
      if (s.source !== "insights") {
        const bc = await ig("GET", `${API}/${mid}`, tok, { fields: "like_count,comments_count" });
        Object.assign(s, { likes: bc.like_count ?? null, comments: bc.comments_count ?? null, source: "basic" });
        if (s.no_perm) await setSetting(bk("instagram_insights", b.id), { ok: false, checked_at: iso(now()),
          note: "Instagram results need the instagram_business_manage_insights permission; using likes and comments only." });
        delete s.no_perm;
      }
      if (comments) {        // what the audience actually said: the most-liked comments feed the learning loop
        try {
          const c = await ig("GET", `${API}/${mid}/comments`, tok, { fields: "text,like_count,timestamp", limit: "25" });
          s.top_comments = (c.data ?? []).filter((x: Json) => x.text).sort((a: Json, b: Json) => (b.like_count ?? 0) - (a.like_count ?? 0))
            .slice(0, 5).map((x: Json) => String(x.text).replace(/\s+/g, " ").slice(0, 160));
        } catch (e) {
          if ((e as IGError).auth) throw e;
          comments = false; await setSetting(bk("instagram_insights", b.id), { ...cap, comments: false, checked_at: iso(now()) });
        }
      }
      await rest("POST", "rpc/set_reel_stats", { p_job: r.id, p_stats: s }, "return=minimal");
      done[r.id] = s.source;
    } catch (e) { done[r.id] = `error: ${String((e as Error).message ?? e).slice(0, 120)}`; }
  }
  return { due: due.length, done };
}

// ---------------------------------------------------------------- the account's past posts (also those made outside the studio): once a day
// The learning loop reads them, so it learns from everything the audience has already responded to, not only studio reels.
async function libraryBrand(b: Brand): Promise<Json> {
  const LK = bk("ig_library", b.id); const lib = await setting(LK);
  if (minsAgo(lib.at) < 24 * 60) return { due: 0 };
  const st = await setting(bk("instagram_status", b.id));
  if (st.state !== "connected") return { connected: false };
  const [tok, uid] = await connection(b.id, false);
  if (!tok || !uid) return { connected: false };
  const r = await ig("GET", `${API}/${uid}/media`, tok, { fields: "id,caption,media_type,media_product_type,timestamp,like_count,comments_count,permalink", limit: "50" });
  const cap = await setting(bk("instagram_insights", b.id)); let insights = cap.ok !== false;
  const posts: Json[] = [];
  for (const m of r.data ?? []) {
    const p: Json = { id: String(m.id), at: String(m.timestamp ?? "").replace(/\+0000$/, "Z"), type: m.media_product_type ?? m.media_type,
      caption: String(m.caption ?? "").replace(/\s+/g, " ").slice(0, 220), likes: m.like_count ?? null, comments: m.comments_count ?? null, link: m.permalink ?? null };
    if (insights && p.type === "REELS" && posts.filter((x) => x.reach != null).length < 25) {
      try {
        const j = await ig("GET", `${API}/${m.id}/insights`, tok, { metric: CORE });
        for (const x of j.data ?? []) { const v = x.values?.[0]?.value ?? x.total_value?.value; if (typeof v === "number") p[x.name] = v; }
      } catch (e) { if ((e as IGError).auth) throw e; insights = false; }
    }
    posts.push(p);
  }
  await setSetting(LK, { at: iso(now()), posts });
  return { posts: posts.length };
}
export async function libraryTick(list: Brand[]): Promise<Json> {
  const res: Json = {};
  for (const b of list) { try { const r = await libraryBrand(b); if (r.posts) res[b.id] = r; } catch (e) { res[b.id] = { error: String((e as Error).message ?? e).slice(0, 160) }; } }
  return res;
}

export async function statsTick(list: Brand[]): Promise<Json> {
  const res: Json = {};
  for (const b of list) { try { const r = await statsBrand(b); if (r.due) res[b.id] = r; } catch (e) { res[b.id] = { error: String((e as Error).message ?? e) }; } }
  return res;
}

Deno.serve(async (req) => {
  const key = await cronKey().catch(() => null);
  if (!key || req.headers.get("x-cron-key") !== key) return new Response("forbidden", { status: 403 });
  const res: Json = {};
  let list: Brand[] = [];
  try { list = await brands(); } catch (e) { return Response.json({ error: String((e as Error).message ?? e) }); }
  try { res.instagram = await tick(list); } catch (e) { res.instagram = { error: String((e as Error).message ?? e) }; }
  try { res.stats = await statsTick(list); } catch (e) { res.stats = { error: String((e as Error).message ?? e) }; }
  try { res.facebook = await fbTick(list); } catch (e) { res.facebook = { error: String((e as Error).message ?? e) }; }
  try { res.linkedin = await liTick(list); } catch (e) { res.linkedin = { error: String((e as Error).message ?? e) }; }
  try { res.keys = await keysTick(); } catch (e) { res.keys = { error: String((e as Error).message ?? e) }; }
  try { res.library = await libraryTick(list); } catch (e) { res.library = { error: String((e as Error).message ?? e) }; }
  try { res.checks = await checksTick(list); } catch (e) { res.checks = { error: String((e as Error).message ?? e) }; }
  try { res.dm = await dmTick(list); } catch (e) { res.dm = { error: String((e as Error).message ?? e) }; }
  try { res.retention = await retentionTick(); } catch (e) { res.retention = { error: String((e as Error).message ?? e) }; }
  return Response.json(res);
});
