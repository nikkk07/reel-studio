// Shared helpers for the poster: database (PostgREST, service role) and storage.
export const SB = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/$/, "");
export const KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
export const H = { apikey: KEY, Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" };
export type Json = Record<string, any>;
export const now = () => new Date();
export const iso = (d: Date) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
export const minsAgo = (s?: string) => (s ? (Date.now() - Date.parse(s)) / 60000 : Infinity);

export async function rest(method: string, path: string, body?: unknown, prefer = "return=representation") {
  const r = await fetch(`${SB}/rest/v1/${path}`, { method, headers: { ...H, Prefer: prefer }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) throw new Error(`db ${method} ${path.split("?")[0]} -> ${r.status} ${(await r.text()).slice(0, 200)}`);
  const t = await r.text(); return t ? JSON.parse(t) : null;
}
// one account's own settings/secrets: We One Aviation keeps the original keys, every other account gets "<key>@<account>"
export const bk = (base: string, brand?: string) => (!brand || brand === "weone" ? base : `${base}@${brand}`);
export type Brand = { id: string; name: string; links?: Json };
export async function brands(): Promise<Brand[]> { return (await rest("GET", "brands?select=id,name,links&order=sort")) ?? []; }
export async function setting(key: string): Promise<Json> { const r = await rest("GET", `settings?select=value&key=eq.${encodeURIComponent(key)}`); return r?.[0]?.value ?? {}; }
export const setSetting = (key: string, value: Json) => rest("POST", "settings", { key, value, updated_at: iso(now()) }, "resolution=merge-duplicates,return=minimal");
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
  const cr = r.headers.get("content-range") ?? ""; await r.body?.cancel();
  const n = Number(cr.split("/")[1]);
  if (!r.ok || !n) throw new Error(`could not read the stored video (${r.status})`);
  return n;
}
export async function storageRange(path: string, first: number, last: number): Promise<Uint8Array> {
  const r = await fetch(`${SB}/storage/v1/object/reels/${path}`, { headers: { ...H, Range: `bytes=${first}-${last}` } });
  if (!r.ok) throw new Error(`could not read the stored video (${r.status})`);
  return new Uint8Array(await r.arrayBuffer());
}
