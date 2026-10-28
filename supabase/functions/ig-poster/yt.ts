// YouTube Shorts (YouTube Data API v3, resumable upload), one short step per cron call.
// State lives in jobs.output.yt:  scheduled -> uploading (8 MB parts) -> processing -> posted
// Safety: the upload session and the bytes YouTube confirmed are saved after every part, so a timeout or a lost response
// resumes (YouTube is asked how much it has) instead of uploading a second copy. A vertical video of up to 3 minutes
// is a Short automatically. Uploads have their own quota (100 a day per Google project), far above the studio's reels.
import { SB, H, type Json, type Brand, now, iso, minsAgo, rest, setting, setSetting, secret, setPart, bk } from "./db.ts";
import { mediaSize, mediaRange } from "./media.ts";

const YT = Deno.env.get("YT_API") ?? "https://www.googleapis.com/youtube/v3";
const YT_UP = Deno.env.get("YT_UPLOAD") ?? "https://www.googleapis.com/upload/youtube/v3";
const TOKEN_URL = Deno.env.get("GOOGLE_TOKEN_URL") ?? "https://oauth2.googleapis.com/token";
const MAX_ATTEMPTS = 5;
const BACKOFF_MIN = [10, 30, 60, 120];
const CHUNK = 8 * 1024 * 1024;              // a multiple of 256 KB, as YouTube requires for every part but the last
const BUDGET_MS = 50_000;

export class YTError extends Error {
  constructor(msg: string, public status = 0, public transient = true, public auth = false, public restart = false) { super(msg); }
}

// ---------------------------------------------------------------- Google sign-in: a fresh 1-hour access token from the saved refresh token
export async function accessToken(brand: string): Promise<string> {
  const [ref] = await secret(bk("youtube_refresh", brand));
  if (!ref) throw new YTError("YouTube is not connected", 0, false, true);
  const app = await setting("youtube_app"); const [sec] = await secret("youtube_client_secret");
  if (!app.client_id || !sec) throw new YTError("The YouTube app (Client ID and Secret) is not saved in Studio Settings.", 0, false);
  let r: Response;
  try {
    r = await fetch(TOKEN_URL, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: ref, client_id: app.client_id, client_secret: sec }) });
  } catch (e) { throw new YTError(`network problem talking to Google (${(e as Error).name})`); }
  const j: Json = await r.json().catch(() => ({}));
  if (r.ok && j.access_token) return j.access_token;
  if (j.error === "invalid_grant") throw new YTError("Google sign-in expired or was removed", r.status, false, true);
  if (j.error === "invalid_client" || j.error === "unauthorized_client") throw new YTError("Google refused the YouTube app: check the Client ID and Secret in Studio Settings.", r.status, false);
  throw new YTError(`Google sign-in failed (${j.error ?? r.status})`, r.status, r.status >= 500);
}

async function yt(method: string, url: string, tok: string, body?: unknown, extra: Record<string, string> = {}): Promise<[Json, Response]> {
  let r: Response;
  try {
    r = await fetch(url.startsWith("http") ? url : `${YT}${url}`, { method, redirect: "manual",
      headers: { Authorization: `Bearer ${tok}`, ...(body === undefined ? {} : { "Content-Type": "application/json; charset=UTF-8" }), ...extra },
      body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) { throw new YTError(`network problem talking to YouTube (${(e as Error).name})`); }
  const t = await r.text(); let j: Json = {}; try { j = t ? JSON.parse(t) : {}; } catch { /* */ }
  if (!r.ok && r.status !== 308) throw ytErr(r.status, j);
  return [j, r];
}
function ytErr(status: number, j: Json): YTError {
  const reason = j.error?.errors?.[0]?.reason ?? j.error?.status ?? "";
  const msg = String(j.error?.message ?? `HTTP ${status}`).replace(/<[^>]+>/g, "").slice(0, 240);
  if (status === 401) return new YTError("Google sign-in expired", 401, false, true);
  if (["quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded", "uploadLimitExceeded"].includes(reason))
    return new YTError(reason === "uploadLimitExceeded" ? "YouTube's daily upload limit for this channel was reached; trying again later." : "YouTube's daily API quota was reached; trying again later.", status, true);
  if (reason === "youtubeSignupRequired") return new YTError("This Google account has no YouTube channel. Create the channel, then Reconnect YouTube.", status, false, true);
  if (status === 403) return new YTError(`YouTube refused: ${msg}`, 403, false);
  return new YTError(`YouTube: ${msg}`, status, status === 429 || status >= 500);
}

// ---------------------------------------------------------------- what the Short says
const CATEGORY: Record<string, string> = { bookmycharter: "19" };          // Travel & Events; everyone else Education (27)
const clean = (s: string) => String(s ?? "").replace(/[<>]/g, "").trim();
export function ytTitle(o: Json, b: Brand): string {
  let t = clean(o.title || b.name).replace(/\s+/g, " ");
  if (t.length > 100) t = t.slice(0, 99).replace(/\s+\S*$/, "") + "…";
  return t || b.name;
}
export function ytDescription(o: Json, b: Brand): string {
  const site = b.website && /^https?:\/\//.test(b.website) ? b.website : "";
  const lines = clean(o.ig_caption ?? o.title ?? "").split("\n")
    .map((l) => /^.*Follow\s+@\S+.*$/i.test(l) ? `Subscribe to ${b.name} for more.` : l)
    .map((l) => /^💬\s*Comment\s+\S+.*DM/i.test(l) ? (site ? `👉 Details: ${site}` : "") : l)       // Instagram DMs do not exist on YouTube
    .map((l) => l.replace(/@(\w+)/g, "$1"));
  let d = lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  if (site && !d.includes(site)) d += `\n\n${site}`;
  if (!/#shorts\b/i.test(d)) d += "\n\n#Shorts";
  return new TextEncoder().encode(d).length > 4900 ? d.slice(0, 4000) : d;
}
const tagsOf = (o: Json) => [...new Set(String(o.ig_caption ?? "").match(/#[\p{L}\p{N}_]+/gu) ?? [])].map((x) => x.slice(1)).slice(0, 12);

// ---------------------------------------------------------------- one reel, one step
const shortUrl = (id: string) => `https://www.youtube.com/shorts/${id}`;
const sentOf = (r: Response) => { const m = /bytes=0-(\d+)/.exec(r.headers.get("range") ?? ""); return m ? Number(m[1]) + 1 : 0; };

async function step(job: Json, tok: string, b: Brand): Promise<string> {
  const o = job.output ?? {}; let g: Json = { ...(o.yt ?? {}) };
  const file = o.ig_video ?? o.video;
  if (g.state === "scheduled") {
    if (!o.ig_caption && minsAgo(o.decided_at) < 15) return "waiting for the caption";
    if (o.pruned) throw new YTError("The video was removed after 3 days, so it cannot be uploaded any more.", 0, false);
    g = { ...g, state: "uploading", attempts: Number(g.attempts ?? 0) + 1, started_at: iso(now()) }; delete g.error;
    if (!(await setPart(job, "yt", g, "scheduled"))) return "taken";
  }
  if (g.state === "uploading") {
    if (!g.upload_url) {
      const size = await mediaSize(o, file);
      const lang = (b as Json).lang === "hinglish" ? "hi" : "en";        // Hinglish voice-over: Hindi audio
      const [, r] = await yt("POST", `${YT_UP}/videos?uploadType=resumable&part=snippet,status`, tok, {
        snippet: { title: ytTitle(o, b), description: ytDescription(o, b), tags: tagsOf(o), categoryId: CATEGORY[b.id] ?? "27", defaultLanguage: lang, defaultAudioLanguage: lang },
        status: { privacyStatus: "public", selfDeclaredMadeForKids: false, embeddable: true },
      }, { "X-Upload-Content-Length": String(size), "X-Upload-Content-Type": "video/mp4" });
      const loc = r.headers.get("location");
      if (!loc) throw new YTError("YouTube did not open an upload session");
      g = { ...g, upload_url: loc, size, sent: 0, session_at: iso(now()) }; await setPart(job, "yt", g);
    } else if (g.part_sent) {                // a lost response: ask YouTube how much it really has
      const [j, r] = await yt("PUT", g.upload_url, tok, undefined, { "Content-Range": `bytes */${g.size}` });
      if (r.status === 200 || r.status === 201) return await done(job, g, j);
      g = { ...g, sent: sentOf(r) }; delete g.part_sent; await setPart(job, "yt", g);
    }
    const t0 = Date.now();
    while (g.sent < g.size && Date.now() - t0 < BUDGET_MS) {
      const first = g.sent, last = Math.min(g.size, first + CHUNK) - 1;
      const bytes = await mediaRange(o, file, first, last);
      g = { ...g, part_sent: true }; await setPart(job, "yt", g);
      let r: Response; let j: Json = {};
      try {
        r = await fetch(g.upload_url, { method: "PUT", redirect: "manual", headers: { Authorization: `Bearer ${tok}`, "Content-Type": "video/mp4", "Content-Range": `bytes ${first}-${last}/${g.size}` }, body: new Blob([bytes as BlobPart]) });
        const t = await r.text(); try { j = t ? JSON.parse(t) : {}; } catch { /* */ }
      } catch (e) { throw new YTError(`network problem uploading to YouTube (${(e as Error).name})`); }
      if (r.status === 200 || r.status === 201) return await done(job, g, j);
      if (r.status === 404 || r.status === 410) throw new YTError("YouTube's upload session expired; starting the upload again", r.status, true, false, true);
      if (r.status !== 308) throw ytErr(r.status, j);
      g = { ...g, sent: sentOf(r) }; delete g.part_sent; await setPart(job, "yt", g);
    }
    return `uploaded ${Math.round(100 * g.sent / g.size)}%`;
  }
  if (g.state === "processing") {
    const [j] = await yt("GET", `/videos?part=status,processingDetails&id=${encodeURIComponent(g.video_id)}`, tok);
    const v = j.items?.[0];
    const us = v?.status?.uploadStatus;
    if (!v || us === "rejected" || us === "failed" || us === "deleted")
      throw new YTError(`YouTube did not publish the video (${v?.status?.rejectionReason ?? v?.status?.failureReason ?? us ?? "not found"})`, 0, false);
    if (us === "processed" || minsAgo(g.uploaded_at) > 60) {
      if (o.yt_thumb && !g.thumb) g.thumb = await setThumb(tok, g.video_id, o.yt_thumb);
      g = { ...g, state: "posted", posted_at: iso(now()), permalink: shortUrl(g.video_id) }; delete g.error;
      await setPart(job, "yt", g); return "posted";
    }
    return `processing (${us ?? "?"})`;
  }
  return "nothing to do";
}
// the AI thumbnail made on the Mac (ThumbAPI, optional): never blocks the post; YouTube only accepts custom
// thumbnails from channels that verified a phone number, so a refusal is recorded, not retried
export async function setThumb(tok: string, videoId: string, path: string): Promise<string> {
  try {
    const f = await fetch(`${SB}/storage/v1/object/reels/${path}`, { headers: H });
    if (!f.ok) return `skipped: thumbnail file missing (${f.status})`;
    const r = await fetch(`${YT_UP}/thumbnails/set?videoId=${encodeURIComponent(videoId)}`, { method: "POST",
      headers: { Authorization: `Bearer ${tok}`, "Content-Type": "image/jpeg" }, body: new Uint8Array(await f.arrayBuffer()) });
    const j: Json = await r.json().catch(() => ({}));
    if (r.ok) return "set";
    return `refused: ${String(j.error?.message ?? r.status).replace(/<[^>]+>/g, "").slice(0, 160)}`;
  } catch (e) { return `skipped: ${(e as Error).name}`; }
}
async function done(job: Json, g: Json, j: Json): Promise<string> {
  if (!j.id) throw new YTError("YouTube finished the upload but did not return the video id", 0, false);
  for (const k of ["upload_url", "part_sent", "sent"]) delete g[k];
  g = { ...g, state: "processing", video_id: j.id, uploaded_at: iso(now()), permalink: shortUrl(j.id) };
  await setPart(job, "yt", g); return "uploaded; YouTube is processing";
}

async function fail(job: Json, e: YTError, brand: string) {
  const g: Json = { ...(job.output?.yt ?? {}) }; const msg = e.message.slice(0, 300); const att = Number(g.attempts ?? 1);
  if (e.restart) for (const k of ["upload_url", "size", "sent", "part_sent", "session_at"]) delete g[k];
  const resume = ["uploading", "processing"].includes(g.state) ? g.state : null;
  if (e.auth) {
    const st = await setting(bk("youtube_status", brand));
    await setSetting(bk("youtube_status", brand), { ...st, state: "expired", note: "Google sign-in for YouTube expired or was removed. Tap Reconnect YouTube in this account's Settings.", checked_at: iso(now()) });
    Object.assign(g, { state: "scheduled", at: iso(new Date(Date.now() + 10 * 60e3)), attempts: Math.max(0, att - 1), error: "Waiting for YouTube to be connected again." });
  } else if (att >= MAX_ATTEMPTS || (!e.transient && att >= 2) || (!e.transient && e.status === 0 && !resume)) Object.assign(g, { state: "failed", error: `Posting failed: ${msg}` });
  else Object.assign(g, { state: "scheduled", at: iso(new Date(Date.now() + BACKOFF_MIN[Math.min(BACKOFF_MIN.length - 1, att - 1)] * 60e3)), error: msg });
  if (g.state === "scheduled" && resume && !e.restart) g.resume = resume;
  await setPart(job, "yt", g);
}

async function ytBrand(b: Brand): Promise<Json> {
  const reels: Json[] = await rest("GET", `jobs?select=id,output&kind=eq.reel&brand=eq.${b.id}&status=eq.done&output->yt->>state=in.(scheduled,uploading,processing)&order=updated_at.asc&limit=20`);
  const due = reels.filter((r) => r.output.yt.state !== "scheduled" || Date.parse(r.output.yt.at ?? 0) <= Date.now());
  if (!due.length) return { due: 0 };
  const st = await setting(bk("youtube_status", b.id));
  const lang = (await rest("GET", `brands?select=lang:profile->>language&id=eq.${encodeURIComponent(b.id)}`))?.[0]?.lang;
  const acct = { ...b, lang } as Brand;
  let tok: string;
  try { tok = await accessToken(b.id); }
  catch (e) {
    const err = e as YTError;
    if (err.auth && st.state === "connected") await setSetting(bk("youtube_status", b.id), { ...st, state: "expired", checked_at: iso(now()),
      note: "Google sign-in for YouTube expired or was removed. Tap Reconnect YouTube in this account's Settings. (If the Google app is still in Testing, Google ends sign-ins after 7 days: publish the app.)" });
    for (const r of due) if (r.output.yt.state === "scheduled" && !r.output.yt.error)
      await setPart(r, "yt", { ...r.output.yt, error: `Waiting for YouTube: ${err.message}` }, "scheduled");
    return { connected: false, due: due.length };
  }
  const log: Json = {};
  for (const r of due.slice(0, 2)) {
    const g = r.output.yt;
    if (g.state === "scheduled" && g.resume) {          // a retry continues where it stopped
      const next = { ...g, state: g.resume, attempts: Number(g.attempts ?? 0) + 1 }; delete next.resume; delete next.error;
      if (!(await setPart(r, "yt", next, "scheduled"))) continue;
    }
    try { log[r.id] = await step(r, tok, acct); }
    catch (e) { const err = e instanceof YTError ? e : new YTError(String((e as Error).message ?? e)); await fail(r, err, b.id); log[r.id] = `error: ${err.message}`; }
  }
  if (st.state !== "connected" && Object.values(log).some((x) => !String(x).startsWith("error"))) {
    const cur = await setting(bk("youtube_status", b.id));
    if (cur.state !== "connected") await setSetting(bk("youtube_status", b.id), { ...cur, state: "connected", note: undefined, checked_at: iso(now()) });
  }
  return { connected: true, due: due.length, log };
}
export async function ytTick(list: Brand[]): Promise<Json> {
  const res: Json = {};
  for (const b of list) { try { const r = await ytBrand(b); if (r.due) res[b.id] = r; } catch (e) { res[b.id] = { error: String((e as Error).message ?? e).slice(0, 160) }; } }
  return res;
}

// "Re-check" on the site, plus a routine check every 6 hours: a fresh Google token and the channel it belongs to.
export async function ytCheck(b: Brand): Promise<string | null> {
  const SK = bk("youtube_status", b.id); const st = await setting(SK);
  const asked = !!st.recheck_at; const state = st.state ?? "not_connected"; const at = iso(now());
  if (!asked && (state === "not_connected" || minsAgo(st.checked_at) < (state === "connected" ? 360 : 30))) return null;
  const [ref] = await secret(bk("youtube_refresh", b.id));
  if (!ref) { await setSetting(SK, { state: "not_connected", checked_at: at }); return "not_connected"; }
  try {
    const tok = await accessToken(b.id);
    const [j] = await yt("GET", "/channels?part=snippet&mine=true", tok);
    const c = j.items?.[0];
    if (!c) { await setSetting(SK, { ...st, state: "error", note: "This Google account has no YouTube channel. Create it, then Reconnect YouTube.", recheck_at: undefined, checked_at: at }); return "error"; }
    await setSetting(SK, { ...st, state: "connected", channel_id: c.id, channel_title: c.snippet?.title ?? st.channel_title, handle: c.snippet?.customUrl ?? st.handle, note: undefined, recheck_at: undefined, checked_at: at });
    return "connected";
  } catch (e) {
    const err = e as YTError;
    if (err.auth) { await setSetting(SK, { ...st, state: "expired", note: "Google sign-in for YouTube expired or was removed. Tap Reconnect YouTube. (Apps left in Testing lose sign-ins after 7 days: publish the Google app.)", recheck_at: undefined, checked_at: at }); return "expired"; }
    if (asked || state !== "connected") { await setSetting(SK, { ...st, state: "error", note: `YouTube could not be checked right now: ${err.message}`, recheck_at: undefined, checked_at: at }); return "error"; }
    await setSetting(SK, { ...st, checked_at: at }); return "kept";      // one blip on a routine check never raises a false alarm
  }
}
