// Cloudflare R2 (S3 API) for the cloud poster: signed links for Instagram / Facebook (they fetch the video themselves),
// ranged reads for LinkedIn's part uploads, deletes for the retention step, and a check when the keys are saved.
// AWS Signature V4 with WebCrypto, no packages. Keys come from the database and never appear in responses or logs.
import { type Json, setting, secret } from "./db.ts";

const enc = new TextEncoder();
const hex = (b: ArrayBuffer) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
const sha = async (s: string) => hex(await crypto.subtle.digest("SHA-256", enc.encode(s)));
async function hmac(key: ArrayBuffer | Uint8Array, msg: string): Promise<ArrayBuffer> {
  const k = await crypto.subtle.importKey("raw", key instanceof Uint8Array ? key.slice().buffer : key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return crypto.subtle.sign("HMAC", k, enc.encode(msg));
}
const q = (s: string) => encodeURIComponent(s).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
const qPath = (s: string) => s.split("/").map(q).join("/");

export type R2Conf = { account_id: string; bucket: string; key_id: string; secret: string; host?: string };

async function signingKey(secret_: string, day: string, region: string) {
  let k = await hmac(enc.encode("AWS4" + secret_), day);
  for (const p of [region, "s3", "aws4_request"]) k = await hmac(k, p);
  return k;
}
const host = (c: R2Conf) => c.host ?? `${c.account_id}.r2.cloudflarestorage.com`;
const uriOf = (c: R2Conf, key: string) => `/${q(c.bucket)}${key ? "/" + qPath(key) : ""}`;
const stamps = (t = new Date()) => { const s = t.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, ""); return [s, s.slice(0, 8)]; };

export async function presign(c: R2Conf, key: string, seconds = 7200, opts: { now?: Date; uri?: string; region?: string } = {}): Promise<string> {
  const region = opts.region ?? "auto"; const [stamp, day] = stamps(opts.now); const scope = `${day}/${region}/s3/aws4_request`;
  const params: Record<string, string> = { "X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": `${c.key_id}/${scope}`, "X-Amz-Date": stamp,
    "X-Amz-Expires": String(Math.min(seconds, 604800)), "X-Amz-SignedHeaders": "host" };
  const qs = Object.keys(params).sort().map((k) => `${q(k)}=${q(params[k])}`).join("&");
  const uri = opts.uri ?? uriOf(c, key);
  const creq = ["GET", uri, qs, `host:${host(c)}\n`, "host", "UNSIGNED-PAYLOAD"].join("\n");
  const sts = ["AWS4-HMAC-SHA256", stamp, scope, await sha(creq)].join("\n");
  const sig = hex(await hmac(await signingKey(c.secret, day, region), sts));
  return `https://${host(c)}${uri}?${qs}&X-Amz-Signature=${sig}`;
}

export async function r2fetch(c: R2Conf, method: string, key: string, query: Record<string, string> = {}, extra: Record<string, string> = {}): Promise<Response> {
  const [stamp, day] = stamps(); const scope = `${day}/auto/s3/aws4_request`; const ph = "UNSIGNED-PAYLOAD";
  const qs = Object.keys(query).sort().map((k) => `${q(k)}=${q(query[k])}`).join("&");
  const uri = uriOf(c, key);
  const canon = `host:${host(c)}\nx-amz-content-sha256:${ph}\nx-amz-date:${stamp}\n`; const signed = "host;x-amz-content-sha256;x-amz-date";
  const creq = [method, uri, qs, canon, signed, ph].join("\n");
  const sts = ["AWS4-HMAC-SHA256", stamp, scope, await sha(creq)].join("\n");
  const sig = hex(await hmac(await signingKey(c.secret, day, "auto"), sts));
  return fetch(`https://${host(c)}${uri}${qs ? "?" + qs : ""}`, { method, headers: { ...extra, "x-amz-content-sha256": ph, "x-amz-date": stamp,
    Authorization: `AWS4-HMAC-SHA256 Credential=${c.key_id}/${scope}, SignedHeaders=${signed}, Signature=${sig}` } });
}

// the bucket set up on the site; `checked` = only once the cloud has confirmed the keys work
export async function r2conf(checked = true): Promise<R2Conf | null> {
  const st = await setting("r2");
  if (!st.account_id || !st.bucket || (checked && st.state !== "ok")) return null;
  const [kid] = await secret("r2_access_key_id"); const [sec] = await secret("r2_secret_access_key");
  return kid && sec ? { account_id: st.account_id, bucket: st.bucket, key_id: kid, secret: sec, host: Deno.env.get("R2_HOST") ?? undefined } : null;
}

export async function r2list(c: R2Conf, prefix: string): Promise<string[]> {
  const r = await r2fetch(c, "GET", "", { "list-type": "2", prefix, "max-keys": "1000" });
  const t = await r.text();
  if (!r.ok) throw new Error(`R2 list failed (HTTP ${r.status})`);
  return [...t.matchAll(/<Key>([^<]+)<\/Key>/g)].map((m) => m[1].replace(/&amp;/g, "&"));
}
export async function r2delete(c: R2Conf, key: string): Promise<void> {
  const r = await r2fetch(c, "DELETE", key); await r.body?.cancel();
  if (!r.ok && r.status !== 404) throw new Error(`R2 delete failed (HTTP ${r.status})`);
}
export async function r2check(c: R2Conf): Promise<Json> {
  try {
    const r = await r2fetch(c, "GET", "", { "list-type": "2", "max-keys": "1" }); const t = await r.text();
    if (r.ok) return { state: "ok", note: null };
    if (r.status === 403 || r.status === 401) return { state: "bad", note: "Cloudflare did not accept this Access Key ID and Secret for the bucket. Create an R2 API token with Object Read & Write for this bucket." };
    if (r.status === 404 || /NoSuchBucket/.test(t)) return { state: "bad", note: "No bucket with this name in that Cloudflare account. Check the bucket name and the Account ID." };
    return { state: "checking", note: `Cloudflare did not answer properly (HTTP ${r.status}); trying again.` };
  } catch (e) { return { state: "checking", note: `Cloudflare did not answer (${(e as Error).name}); trying again.` }; }
}
