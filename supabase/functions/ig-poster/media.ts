// A reel's video wherever it is stored: Cloudflare R2 (output.store = "r2") or Supabase Storage (older reels, or R2 not set up).
// Thumbnails always stay in Supabase (tiny; the dashboards show them after the video is gone).
import { SB, H, type Json, storageSize, storageRange } from "./db.ts";
import { r2conf, presign, r2fetch } from "./r2.ts";

async function conf() {
  const c = await r2conf(false);
  if (!c) throw new Error("this reel is stored in Cloudflare R2, but R2 is not set up on the site any more");
  return c;
}

export async function mediaUrl(o: Json, path: string, seconds = 7200): Promise<string> {
  if (o?.store === "r2" && !String(path).endsWith("thumb.jpg")) return presign(await conf(), path, seconds);
  const r = await fetch(`${SB}/storage/v1/object/sign/reels/${path}`, { method: "POST", headers: H, body: JSON.stringify({ expiresIn: seconds }) });
  if (!r.ok) throw new Error(`storage link failed (${r.status})`);
  const j = await r.json(); const u: string = j.signedURL ?? j.signedUrl ?? "";
  return u.startsWith("http") ? u : `${SB}/storage/v1${u}`;
}

export async function mediaSize(o: Json, path: string): Promise<number> {
  if (o?.store !== "r2") return storageSize(path);
  const r = await r2fetch(await conf(), "GET", path, {}, { Range: "bytes=0-0" });
  const cr = r.headers.get("content-range") ?? ""; await r.body?.cancel();
  const n = Number(cr.split("/")[1]);
  if (!r.ok || !n) throw new Error(`could not read the stored video (${r.status})`);
  return n;
}

export async function mediaRange(o: Json, path: string, first: number, last: number): Promise<Uint8Array> {
  if (o?.store !== "r2") return storageRange(path, first, last);
  const r = await r2fetch(await conf(), "GET", path, {}, { Range: `bytes=${first}-${last}` });
  if (!r.ok) throw new Error(`could not read the stored video (${r.status})`);
  return new Uint8Array(await r.arrayBuffer());
}
