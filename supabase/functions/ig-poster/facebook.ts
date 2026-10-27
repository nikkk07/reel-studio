// Facebook Page Reels (Graph API video_reels): start -> upload (Facebook fetches our signed file URL) -> finish(PUBLISHED)
// -> wait until published. State lives in jobs.output.fb; each step is saved, so a lost response is checked, never repeated.
import { type Json, type Brand, now, iso, minsAgo, rest, setting, setSetting, secret, setPart, bk } from "./db.ts";

const FB = Deno.env.get("FB_API") ?? "https://graph.facebook.com/v26.0";
const RUP = Deno.env.get("FB_RUPLOAD") ?? "https://rupload.facebook.com/video-upload/v26.0";
const SB = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/$/, "");
const KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const MAX_ATTEMPTS = 5;
const BACKOFF_MIN = [10, 30, 60, 120];
const TRANSIENT = new Set([1, 2, 4, 17, 32, 341, 368, 613, 6000, 6001]);

class FBError extends Error {
  constructor(msg: string, public transient = true, public auth = false, public restart = false) { super(msg); }
}

async function fb(method: "GET" | "POST", url: string, tok: string, params: Json = {}, headers: Json = {}) {
  let r: Response;
  try {
    r = method === "GET" ? await fetch(`${url}?${new URLSearchParams({ ...params, access_token: tok })}`)
      : await fetch(url, { method: "POST", headers, body: Object.keys(params).length ? new URLSearchParams({ ...params, access_token: tok }) : undefined });
  } catch (e) { throw new FBError(`network problem talking to Facebook (${(e as Error).name})`); }
  let j: Json = {}; try { j = await r.json(); } catch { /* */ }
  if (!r.ok || j.error) {
    const e = j.error ?? {}; const msg = String(e.error_user_msg ?? e.message ?? `HTTP ${r.status}`).split(tok).join("***").slice(0, 300);
    const auth = e.code === 190 || r.status === 401;
    throw new FBError(`Facebook: ${msg}`, !auth && (r.status >= 500 || TRANSIENT.has(e.code) || !!e.is_transient), auth);
  }
  return j;
}

export function fbCaption(caption: string, name = "We One Aviation"): string {
  const follow = name === "We One Aviation" ? "Follow We One Aviation for daily aviation facts." : `Follow ${name}.`;
  return caption.replace(/^.*Follow\s+@\S+.*$/gmi, follow).replace(/@(\w+)/g, "$1").slice(0, 5000);
}
async function sign(path: string) {
  const r = await fetch(`${SB}/storage/v1/object/sign/reels/${path}`, { method: "POST", headers: { apikey: KEY, Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" }, body: JSON.stringify({ expiresIn: 7200 }) });
  if (!r.ok) throw new FBError(`storage link failed (${r.status})`);
  const j = await r.json(); const u: string = j.signedURL ?? j.signedUrl ?? "";
  return u.startsWith("http") ? u : `${SB}/storage/v1${u}`;
}

async function step(job: Json, tok: string, page: string, b: Brand): Promise<string> {
  const o = job.output ?? {}; let g: Json = { ...(o.fb ?? {}) };
  if (g.state === "scheduled") {
    if (!o.ig_caption && minsAgo(o.decided_at) < 15) return "waiting for the caption";
    g = { ...g, state: "uploading", attempts: Number(g.attempts ?? 0) + 1, started_at: iso(now()) }; delete g.error;
    if (!(await setPart(job, "fb", g, "scheduled"))) return "taken";
  }
  if (g.state === "uploading") {
    if (!g.video_id) {
      const s = await fb("POST", `${FB}/${page}/video_reels`, tok, { upload_phase: "start" });
      g = { ...g, video_id: s.video_id }; await setPart(job, "fb", g);
    }
    let uploaded = false;
    if (g.upload_sent) {        // a lost response: ask Facebook instead of uploading again
      const st = await fb("GET", `${FB}/${g.video_id}`, tok, { fields: "status" });
      uploaded = st.status?.uploading_phase?.status === "complete";
    }
    if (!uploaded) {
      g = { ...g, upload_sent: true }; await setPart(job, "fb", g);
      const url = await sign(o.ig_video ?? o.video);
      const u = await fb("POST", `${RUP}/${g.video_id}`, tok, {}, { Authorization: `OAuth ${tok}`, file_url: url });
      if (!u.success) throw new FBError("Facebook did not take the video");
    }
    g = { ...g, state: "processing", finish_sent: true, finished_at: iso(now()) }; await setPart(job, "fb", g);
    await fb("POST", `${FB}/${page}/video_reels`, tok, { video_id: g.video_id, upload_phase: "finish", video_state: "PUBLISHED",
      description: fbCaption(o.ig_caption ?? String(o.title ?? ""), b.name), title: String(o.title ?? "").slice(0, 250) });
    return "uploaded; publishing";
  }
  if (g.state === "processing") {
    const st = await fb("GET", `${FB}/${g.video_id}`, tok, { fields: "status,permalink_url" });
    const vs = st.status?.video_status; const pub = st.status?.publishing_phase?.publish_status ?? st.status?.publishing_phase?.status;
    if (["error", "upload_failed", "expired"].includes(vs) || pub === "error")
      throw new FBError(`Facebook could not process the video (${st.status?.processing_phase?.error?.message ?? vs ?? pub})`, true, false, true);
    if (pub === "published" || pub === "complete") {
      const link = st.permalink_url ? (String(st.permalink_url).startsWith("http") ? st.permalink_url : `https://www.facebook.com${st.permalink_url}`) : null;
      g = { ...g, state: "posted", permalink: link, posted_at: iso(now()) }; delete g.error;
      await setPart(job, "fb", g); return "posted";
    }
    if (!g.finish_sent) {
      g = { ...g, finish_sent: true }; await setPart(job, "fb", g);
      await fb("POST", `${FB}/${page}/video_reels`, tok, { video_id: g.video_id, upload_phase: "finish", video_state: "PUBLISHED", description: fbCaption(o.ig_caption ?? "", b.name) });
    }
    if (minsAgo(g.finished_at) > 30) throw new FBError("Facebook took longer than 30 minutes to publish the reel", true, false, true);
    return `processing (${vs ?? "?"})`;
  }
  return "nothing to do";
}

async function fail(job: Json, e: FBError, brand: string) {
  const g: Json = { ...(job.output?.fb ?? {}) }; const msg = e.message.slice(0, 300); const att = Number(g.attempts ?? 1);
  if (e.restart) for (const k of ["video_id", "upload_sent", "finish_sent", "finished_at"]) delete g[k];
  if (e.auth) {
    const st = await setting(bk("facebook_status", brand));
    await setSetting(bk("facebook_status", brand), { ...st, state: "expired", note: "Facebook connection stopped working (password change or removed access). Connect Facebook again in this account's Settings." });
    Object.assign(g, { state: "scheduled", at: iso(new Date(Date.now() + 10 * 60e3)), attempts: Math.max(0, att - 1), error: "Waiting for Facebook to be connected again." });
  } else if (att >= MAX_ATTEMPTS || (!e.transient && att >= 2)) Object.assign(g, { state: "failed", error: `Posting failed: ${msg}` });
  else Object.assign(g, { state: "scheduled", at: iso(new Date(Date.now() + BACKOFF_MIN[Math.min(BACKOFF_MIN.length - 1, att - 1)] * 60e3)), error: msg });
  await setPart(job, "fb", g);
}

async function fbBrand(b: Brand): Promise<Json> {
  const reels: Json[] = await rest("GET", `jobs?select=id,output&kind=eq.reel&brand=eq.${b.id}&status=eq.done&output->fb->>state=in.(scheduled,uploading,processing)&order=updated_at.asc&limit=20`);
  const due = reels.filter((r) => r.output.fb.state !== "scheduled" || Date.parse(r.output.fb.at ?? 0) <= Date.now());
  if (!due.length) return { due: 0 };
  const st = await setting(bk("facebook_status", b.id)); const [tok] = await secret(bk("facebook_page_token", b.id));
  if (!tok || !st.page_id || st.state === "not_connected") {
    for (const r of due) if (r.output.fb.state === "scheduled" && !r.output.fb.error)
      await setPart(r, "fb", { ...r.output.fb, error: "Waiting for Facebook to be connected." }, "scheduled");
    return { connected: false, due: due.length };
  }
  const log: Json = {}; let worked = false;
  for (const r of due.slice(0, 3)) {
    try { log[r.id] = await step(r, tok, st.page_id, b); worked = true; }
    catch (e) { const err = e instanceof FBError ? e : new FBError(String((e as Error).message ?? e)); await fail(r, err, b.id); log[r.id] = `error: ${err.message}`; }
  }
  if (worked && st.state !== "connected") {       // Facebook just accepted a call with this token: the connection works
    const cur = await setting(bk("facebook_status", b.id));
    if (cur.state !== "connected") await setSetting(bk("facebook_status", b.id), { ...cur, state: "connected", note: undefined, checked_at: iso(now()) });
  }
  return { connected: true, due: due.length, log };
}

// "Re-check" on the site, and a routine check (every 6 h when connected, every 30 min when not working):
// asks Facebook for the page with the page token. Works while the Mac is offline.
export async function fbCheck(b: Brand): Promise<string | null> {
  const SK = bk("facebook_status", b.id); const st = await setting(SK);
  const asked = !!st.recheck_at; const state = st.state ?? "not_connected"; const at = iso(now());
  if (!asked && (state === "not_connected" || (state === "connected" && minsAgo(st.checked_at) < 360))) return null;
  const [tok] = await secret(bk("facebook_page_token", b.id));
  if (!tok || !st.page_id) {
    if (!asked && ["not_connected", "duplicate"].includes(state)) return null;
    await setSetting(SK, state === "duplicate" ? { ...st, recheck_at: undefined, checked_at: at } : { state: "not_connected", checked_at: at });
    return state === "duplicate" ? "duplicate" : "not_connected";
  }
  const age = minsAgo(st.checked_at);
  if (!asked && (state === "connected" ? age < 360 : age < 30)) return null;
  try {
    const p = await fb("GET", `${FB}/${st.page_id}`, tok, { fields: "id,name" });
    await setSetting(SK, { ...st, state: "connected", page_name: p.name ?? st.page_name, note: undefined, recheck_at: undefined, checked_at: at });
    return "connected";
  } catch (e) {
    const err = e as FBError;
    if (err.auth) {
      await setSetting(SK, { ...st, state: "expired", note: "Facebook connection stopped working (password change or removed access). Connect Facebook again in this account's Settings.", recheck_at: undefined, checked_at: at });
      return "expired";
    }
    if (asked || state !== "connected") {
      await setSetting(SK, { ...st, state: "error", note: `Facebook could not be checked right now: ${err.message}`, recheck_at: undefined, checked_at: at });
      return "error";
    }
    await setSetting(SK, { ...st, checked_at: at });      // one network blip on a routine check never raises a false alarm
    return "kept";
  }
}
export async function fbTick(list: Brand[]): Promise<Json> {
  const res: Json = {};
  for (const b of list) { try { const r = await fbBrand(b); if (r.due) res[b.id] = r; } catch (e) { res[b.id] = { error: String((e as Error).message ?? e) }; } }
  return res;
}
