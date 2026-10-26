// LinkedIn Company Page posting (Community Management API: Videos API + Posts API), one short step per cron call.
// State lives in jobs.output.li:  scheduled -> uploading (parts) -> processing -> posting -> posted
// Safety: progress (video urn, uploaded parts) is saved after every part, so a timeout resumes instead of restarting;
// before creating a post, the page's recent posts are checked for this video, so a lost response never double-posts.
import { type Json, type Brand, now, iso, minsAgo, rest, setting, setSetting, secret, setSecret, setPart, storageSize, storageRange, bk } from "./db.ts";

const LI_API = Deno.env.get("LI_API") ?? "https://api.linkedin.com/rest";
const LI_OAUTH = Deno.env.get("LI_OAUTH") ?? "https://www.linkedin.com/oauth/v2";
const VERSION = Deno.env.get("LI_VERSION") ?? "202610";
const MAX_ATTEMPTS = 5;
const BACKOFF_MIN = [10, 30, 60, 120];
const BUDGET_MS = 55_000;                 // upload time per call; the next call continues

export class LIError extends Error {
  constructor(msg: string, public status = 0, public transient = true, public auth = false, public restart = false) { super(msg); }
}

async function li(method: string, path: string, tok: string, body?: unknown): Promise<[Json, Response]> {
  let r: Response;
  try {
    r = await fetch(path.startsWith("http") ? path : `${LI_API}${path}`, { method, headers: {
      Authorization: `Bearer ${tok}`, "LinkedIn-Version": VERSION, "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) { throw new LIError(`network problem talking to LinkedIn (${(e as Error).name})`); }
  let j: Json = {}; const t = await r.text(); try { j = t ? JSON.parse(t) : {}; } catch { /* */ }
  if (!r.ok) {
    const msg = String(j.message ?? `HTTP ${r.status}`).split(tok).join("***").slice(0, 300);
    if (r.status === 401) throw new LIError("LinkedIn sign-in expired", 401, false, true);
    if (r.status === 403) throw new LIError(`LinkedIn refused: ${msg}. The app needs the Community Management API and an admin of the page.`, 403, false);
    throw new LIError(`LinkedIn: ${msg}`, r.status, r.status === 429 || r.status >= 500);
  }
  return [j, r];
}

// LinkedIn "little text": escape reserved characters, keep #hashtags as hashtags
export function commentary(caption: string, name = "We One Aviation"): string {
  const text = caption.replace(/^.*Follow\s+@\S+.*$/gmi, name === "We One Aviation" ? "Follow We One Aviation for daily aviation facts." : `Follow ${name}.`)
    .replace(/@(\w+)/g, "$1");
  return text.replace(/[|{}@\[\]()<>\\*_~]/g, (c) => `\\${c}`).replace(/#(?!\w)/g, "\\#").slice(0, 2900);
}

// ---------------------------------------------------------------- connection
async function connection(brand: string): Promise<[string | null, Json]> {
  const SK = bk("linkedin_status", brand), TK = bk("linkedin_token", brand), RK = bk("linkedin_refresh", brand);
  const st = await setting(SK);
  let [tok] = await secret(TK);
  if (!tok) return [null, st];
  // refresh tokens exist only for some apps; when present, renew a week before expiry
  const [ref] = await secret(RK);
  if (ref && st.expires_at && Date.parse(st.expires_at) - Date.now() < 7 * 86400e3) {
    const [app] = [await setting("linkedin_app")]; const [sec] = await secret("linkedin_client_secret");
    const r = await fetch(`${LI_OAUTH}/accessToken`, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: ref, client_id: app.client_id ?? "", client_secret: sec ?? "" }) });
    if (r.ok) {
      const j = await r.json(); tok = j.access_token;
      await setSecret(TK, tok!); if (j.refresh_token) await setSecret(RK, j.refresh_token);
      st.expires_at = iso(new Date(Date.now() + Number(j.expires_in ?? 5184000) * 1000));
      await setSetting(SK, { ...st, state: "connected", note: null });
    }
  }
  if (st.expires_at && Date.parse(st.expires_at) < Date.now()) {
    if (st.state !== "expired") await setSetting(SK, { ...st, state: "expired", note: "LinkedIn sign-in expired (LinkedIn allows 60 days). Tap Reconnect LinkedIn in this account's Settings." });
    return [null, st];
  }
  return [tok, st];
}

// ---------------------------------------------------------------- one reel, one step
async function findPost(tok: string, org: string, video: string): Promise<string | null> {
  const [j] = await li("GET", `/posts?author=${encodeURIComponent(org)}&q=author&count=10&sortBy=LAST_MODIFIED`, tok);
  const p = (j.elements ?? []).find((e: Json) => e?.content?.media?.id === video);
  return p?.id ?? null;
}
const postUrl = (urn: string) => `https://www.linkedin.com/feed/update/${urn}/`;

async function step(job: Json, tok: string, st: Json, b: Brand): Promise<string> {
  const o = job.output ?? {}; let g: Json = { ...(o.li ?? {}) }; const org = st.org_urn;
  const file = o.ig_video ?? o.video;
  if (g.state === "scheduled") {
    if (!o.ig_caption && minsAgo(o.decided_at) < 15) return "waiting for the caption";
    g = { ...g, state: g.resume ?? "uploading", attempts: Number(g.attempts ?? 0) + 1, started_at: iso(now()) }; delete g.error; delete g.resume;
    if (!(await setPart(job, "li", g, "scheduled"))) return "taken";      // a retry resumes where it stopped (never re-finalizes)
    if (!g.video && g.state === "uploading") {
      const size = await storageSize(file);
      const [j] = await li("POST", "/videos?action=initializeUpload", tok, { initializeUploadRequest: { owner: org, fileSizeBytes: size, uploadCaptions: false, uploadThumbnail: false } });
      const v = j.value ?? {};
      g = { ...g, video: v.video, token: v.uploadToken ?? "", parts: (v.uploadInstructions ?? []).map((p: Json) => [p.uploadUrl, p.firstByte, p.lastByte]), etags: [], size };
      await setPart(job, "li", g);
    }
  }
  if (g.state === "uploading") {
    const t0 = Date.now(); const etags: string[] = g.etags ?? [];
    while (etags.length < g.parts.length && Date.now() - t0 < BUDGET_MS) {
      const [url, first, last] = g.parts[etags.length];
      const bytes = await storageRange(file, first, last);
      let r: Response;
      try { r = await fetch(url, { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: new Blob([bytes as BlobPart]) }); }
      catch (e) { throw new LIError(`network problem uploading to LinkedIn (${(e as Error).name})`); }
      await r.body?.cancel();
      if (!r.ok) throw new LIError(`LinkedIn upload part failed (HTTP ${r.status})`, r.status, true, false, r.status === 403 || r.status === 404);
      etags.push(r.headers.get("etag") ?? "");
      g = { ...g, etags }; await setPart(job, "li", g);
    }
    if (etags.length < g.parts.length) return `uploaded ${etags.length}/${g.parts.length} parts`;
    await li("POST", "/videos?action=finalizeUpload", tok, { finalizeUploadRequest: { video: g.video, uploadToken: g.token, uploadedPartIds: etags } });
    g = { ...g, state: "processing", finalized_at: iso(now()) }; await setPart(job, "li", g);
    return "uploaded; LinkedIn is processing";
  }
  if (g.state === "processing") {
    const [v] = await li("GET", `/videos/${encodeURIComponent(g.video)}`, tok);
    if (v.status === "PROCESSING_FAILED") throw new LIError(`LinkedIn could not process the video (${v.processingFailureReason ?? "unknown"})`, 0, true, false, true);
    if (v.status !== "AVAILABLE") {
      if (minsAgo(g.finalized_at) > 30) throw new LIError("LinkedIn took longer than 30 minutes to process the video", 0, true, false, true);
      return `processing (${v.status})`;
    }
    g = { ...g, state: "posting", posting_at: iso(now()) }; await setPart(job, "li", g);
  }
  if (g.state === "posting") {
    let urn: string | null = null;                      // a lost response must never create a second post
    try { urn = await findPost(tok, org, g.video); }
    catch (e) { if (g.post_attempted) throw new LIError(`could not confirm whether the post went out; check the page before retrying (${(e as Error).message})`, 0, false); }
    if (!urn) {
      g = { ...g, post_attempted: true }; await setPart(job, "li", g);
      const [, r] = await li("POST", "/posts", tok, {
        author: org, commentary: commentary(o.ig_caption ?? `${o.title ?? ""}`, b.name), visibility: "PUBLIC",
        distribution: { feedDistribution: "MAIN_FEED", targetEntities: [], thirdPartyDistributionChannels: [] },
        content: { media: { title: String(o.title ?? b.name).slice(0, 200), id: g.video } },
        lifecycleState: "PUBLISHED", isReshareDisabledByAuthor: false });
      urn = r.headers.get("x-restli-id");
    }
    g = { ...g, state: "posted", post: urn, permalink: urn ? postUrl(urn) : null, posted_at: iso(now()) }; delete g.error;
    for (const k of ["parts", "token", "etags"]) delete g[k];
    await setPart(job, "li", g); return "posted";
  }
  return "nothing to do";
}

async function fail(job: Json, e: LIError, brand: string) {
  const g: Json = { ...(job.output?.li ?? {}) }; const msg = e.message.slice(0, 300); const att = Number(g.attempts ?? 1);
  if (e.restart) for (const k of ["video", "token", "parts", "etags", "size", "finalized_at", "resume"]) delete g[k];
  else if (["uploading", "processing", "posting"].includes(g.state)) g.resume = g.state;
  if (e.auth) {
    const st = await setting(bk("linkedin_status", brand));
    await setSetting(bk("linkedin_status", brand), { ...st, state: "expired", note: "LinkedIn sign-in expired or was removed. Tap Reconnect LinkedIn in this account's Settings." });
    Object.assign(g, { state: "scheduled", at: iso(new Date(Date.now() + 10 * 60e3)), attempts: Math.max(0, att - 1), error: "Waiting for LinkedIn to be connected again." });
  } else if (att >= MAX_ATTEMPTS || (!e.transient && att >= 2)) Object.assign(g, { state: "failed", error: `Posting failed: ${msg}` });
  else Object.assign(g, { state: "scheduled", at: iso(new Date(Date.now() + BACKOFF_MIN[Math.min(BACKOFF_MIN.length - 1, att - 1)] * 60e3)), error: msg });
  await setPart(job, "li", g);
}

async function liBrand(b: Brand): Promise<Json> {
  const reels: Json[] = await rest("GET", `jobs?select=id,output&kind=eq.reel&brand=eq.${b.id}&status=eq.done&output->li->>state=in.(scheduled,uploading,processing,posting)&order=updated_at.asc&limit=20`);
  const due = reels.filter((r) => r.output.li.state !== "scheduled" || Date.parse(r.output.li.at ?? 0) <= Date.now());
  if (!due.length) return { due: 0 };
  const [tok, st] = await connection(b.id);
  if (!tok || !st.org_urn) {
    for (const r of due) if (r.output.li.state === "scheduled" && !r.output.li.error)
      await setPart(r, "li", { ...r.output.li, error: "Waiting for LinkedIn to be connected." }, "scheduled");
    return { connected: false, due: due.length };
  }
  const log: Json = {};
  for (const r of due.slice(0, 2)) {
    try { log[r.id] = await step(r, tok, st, b); }
    catch (e) { const err = e instanceof LIError ? e : new LIError(String((e as Error).message ?? e)); await fail(r, err, b.id); log[r.id] = `error: ${err.message}`; }
  }
  return { connected: true, due: due.length, log };
}
export async function liTick(list: Brand[]): Promise<Json> {
  const res: Json = {};
  for (const b of list) { try { const r = await liBrand(b); if (r.due) res[b.id] = r; } catch (e) { res[b.id] = { error: String((e as Error).message ?? e) }; } }
  return res;
}
