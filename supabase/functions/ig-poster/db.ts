// Shared helpers for the poster: database (PostgREST, service role) and storage.
export const SB = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/$/, "");
export const KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
export const H = { apikey: KEY, Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" };
export type Json = Record<string, any>;
export const now = () => new Date();
export const iso = (d: Date) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
export const minsAgo = (s?: string) => (s ? (Date.now() - Date.parse(s)) / 60000 : Infinity);

// ---------------------------------------------------------------- data meter (Supabase Free plan: 5 GB leaves the project a month)
// Every response from Supabase is counted (its size on the wire when known, plus a small allowance for headers) and added to
// egress_log once per run; the answer is the guard level every part of the studio slows down by (0 normal, 1 saver, 2 strict).
export const meter = { bytes: 0, reqs: 0 };
export const guard: Json = { level: 0, known: false };
const HEADERS_B = 400;
export function countBytes(r: Response, bodyLen: number) {
  const wire = Number(r.headers.get("content-length"));
  meter.bytes += HEADERS_B + (wire > 0 ? wire : Math.ceil(bodyLen / 3));    // unknown length: a compressed JSON body is ~1/3
  meter.reqs++;
}
let meterOffUntil = 0;
export async function flushMeter(part = "poster"): Promise<Json> {
  if (Date.now() < meterOffUntil) return guard;
  const b = meter.bytes, n = meter.reqs; meter.bytes = 0; meter.reqs = 0;
  try {
    const r = await rest("POST", "rpc/add_egress", { p_part: part, p_bytes: b, p_reqs: n });
    if (r && typeof r.level === "number") Object.assign(guard, r, { known: true });
  } catch (e) {
    meter.bytes += b; meter.reqs += n;
    if (/ -> 404/.test(String((e as Error).message))) meterOffUntil = Date.now() + 3600e3;     // not installed yet: normal level
  }
  return guard;
}
// a slow job runs every `mins` minutes (at its own offset, so the jobs never all land on one minute); stricter levels stretch it
export function everyMin(mins: number, offset = 0, at = Date.now()): boolean {
  const m = Math.max(1, Math.round(mins * (guard.level >= 2 ? 4 : guard.level >= 1 ? 2 : 1)));
  return Math.floor(at / 60000) % m === offset % m;
}

export async function rest(method: string, path: string, body?: unknown, prefer = "return=representation") {
  const r = await fetch(`${SB}/rest/v1/${path}`, { method, headers: { ...H, Prefer: prefer }, body: body === undefined ? undefined : JSON.stringify(body) });
  const t = await r.text(); countBytes(r, t.length);
  if (!r.ok) throw new Error(`db ${method} ${path.split("?")[0]} -> ${r.status} ${t.slice(0, 200)}`);
  return t ? JSON.parse(t) : null;
}
// one account's own settings/secrets: We One Aviation keeps the original keys, every other account gets "<key>@<account>"
export const bk = (base: string, brand?: string) => (!brand || brand === "weone" ? base : `${base}@${brand}`);
export type Brand = { id: string; name: string; links?: Json; website?: string | null };
export async function brands(): Promise<Brand[]> { return (await rest("GET", "brands?select=id,name,links,website&order=sort")) ?? []; }
// The small settings a run needs (connection states, keys, comment-to-DM) come in one read at the start of each run instead of
// one read per account per check. Big ones (the post library, digests) are never part of it; `fresh` skips the copy.
const PRE_ACCT = ["instagram_status", "instagram_insights", "facebook_status", "linkedin_status", "youtube_status", "website_status", "dm"];
const PRE_ONE = ["studio_keys", "meta_app", "r2", "youtube_app", "linkedin_app"];
const preloaded = (k: string) => PRE_ONE.includes(k) || PRE_ACCT.some((p) => k === p || k.startsWith(`${p}@`));
let cache: Map<string, Json> | null = null;
export async function preloadSettings() {
  cache = null;
  const or = [...PRE_ACCT.flatMap((p) => [`key.eq.${p}`, `key.like.${p}@*`]), `key.in.(${PRE_ONE.join(",")})`].join(",");
  try { cache = new Map(((await rest("GET", `settings?select=key,value&or=(${or})`)) ?? []).map((r: Json) => [r.key, r.value ?? {}])); }
  catch { cache = null; }                                   // read one by one, as before
}
export const dropSettings = () => { cache = null; };
export async function setting(key: string, fresh = false): Promise<Json> {
  if (cache && !fresh && preloaded(key)) return structuredClone(cache.get(key) ?? {});
  const r = await rest("GET", `settings?select=value&key=eq.${encodeURIComponent(key)}`); const v = r?.[0]?.value ?? {};
  if (cache && preloaded(key)) cache.set(key, v);
  return structuredClone(v);
}
export async function setSetting(key: string, value: Json) {
  await rest("POST", "settings", { key, value, updated_at: iso(now()) }, "resolution=merge-duplicates,return=minimal");
  if (cache && preloaded(key)) cache.set(key, JSON.parse(JSON.stringify(value ?? {})));
}

// ---------------------------------------------------------------- reels waiting on a platform
// One light read per run lists every reel with a post under way (only the small per-platform state, never scripts or stats);
// the full reel is read only for the few that are due now.
export const POST_STATES: Record<string, string[]> = {
  ig: ["queued", "scheduled", "posting"], fb: ["scheduled", "uploading", "processing"],
  li: ["scheduled", "uploading", "processing", "posting"], yt: ["scheduled", "uploading", "processing"] };
export async function activeReels(): Promise<Json[]> {
  const sel = `id,brand,${Object.keys(POST_STATES).map((p) => `${p}:output->${p}`).join(",")}`;
  const base = `jobs?select=${sel}&kind=eq.reel&status=eq.done`;
  try {
    const or = Object.entries(POST_STATES).map(([p, s]) => `output->${p}->>state.in.(${s.join(",")})`).join(",");
    return (await rest("GET", `${base}&or=(${or})&order=updated_at.asc&limit=300`)) ?? [];
  } catch {
    const seen = new Map<string, Json>();
    for (const [p, s] of Object.entries(POST_STATES))
      for (const r of (await rest("GET", `${base}&output->${p}->>state=in.(${s.join(",")})&order=updated_at.asc&limit=100`)) ?? []) seen.set(r.id, r);
    return [...seen.values()];
  }
}
// the light rows of one account and platform that pass `isDue` (oldest first)
export async function liteDue(part: string, brand: string, isDue: (p: Json) => boolean, lite?: Json[]): Promise<Json[]> {
  return (lite ?? await activeReels()).filter((r) => r.brand === brand && r[part] && POST_STATES[part].includes(r[part].state) && isDue(r[part]));
}
// the full reels behind light rows, in the same order, still in a posting state
export async function fullReels(rows: Json[], part: string): Promise<Json[]> {
  const ids = rows.map((r) => r.id);
  if (!ids.length) return [];
  const full: Json[] = (await rest("GET", `jobs?select=id,output&id=in.(${ids.join(",")})&status=eq.done`)) ?? [];
  return ids.map((id) => full.find((r) => r.id === id)).filter((r): r is Json => !!r && POST_STATES[part].includes(r.output?.[part]?.state));
}
export async function secret(key: string): Promise<[string | null, string | null]> {
  const r = await rest("GET", `secrets?select=value,updated_at&key=eq.${encodeURIComponent(key)}`);
  return r?.[0]?.value ? [r[0].value, r[0].updated_at] : [null, null];
}
export const setSecret = (key: string, value: string | null) => rest("POST", "secrets", { key, value, updated_at: iso(now()) }, "resolution=merge-duplicates,return=minimal");
// write jobs.output.<platform> only if nobody changed its state meanwhile (overlapping calls can never both act)
export async function setPart(job: Json, part: string, val: Json, expect?: string) {
  const out = { ...(job.output ?? {}), [part]: val };
  const rows = await rest("PATCH", `jobs?id=eq.${job.id}&status=eq.done${expect ? `&output->${part}->>state=eq.${expect}` : ""}`, { output: out });
  if (rows?.length) job.output = out;
  return !!rows?.length;
}
// size of a stored file and byte ranges of it (LinkedIn takes the video in parts)
export async function storageSize(path: string): Promise<number> {
  const r = await fetch(`${SB}/storage/v1/object/reels/${path}`, { headers: { ...H, Range: "bytes=0-0" } });
  const cr = r.headers.get("content-range") ?? ""; await r.body?.cancel(); countBytes(r, 0);
  const n = Number(cr.split("/")[1]);
  if (!r.ok || !n) throw new Error(`could not read the stored video (${r.status})`);
  return n;
}
export async function storageRange(path: string, first: number, last: number): Promise<Uint8Array> {
  const r = await fetch(`${SB}/storage/v1/object/reels/${path}`, { headers: { ...H, Range: `bytes=${first}-${last}` } });
  if (!r.ok) throw new Error(`could not read the stored video (${r.status})`);
  const a = new Uint8Array(await r.arrayBuffer()); countBytes(r, a.length * 3); return a;
}
