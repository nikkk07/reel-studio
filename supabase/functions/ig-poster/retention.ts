// Retention: a reel's video is kept in the cloud only while it is useful (the owner asked for "past 3 days only").
//  * posted (every connected platform is posted / switched off / failed): deleted 3 days after the last post
//  * approved with nowhere to post (no connection): deleted 3 days after approval (download it before then)
//  * stopped or failed: deleted after 1 day
//  * anything else (waiting for review): deleted after 7 days
//  * never while a post is scheduled or in progress: the platforms still need the file
// The thumbnail and every number (views, results, script, hook) stay, so dashboards and learning keep working.
// Older reels kept two copies (reel.mp4 + ig.mp4): the extra copy is removed and the posting copy used everywhere.
import { type Json, SB, H, rest, minsAgo } from "./db.ts";
import { r2conf, r2list, r2delete } from "./r2.ts";

const DAY = 1440;
const PENDING = new Set(["queued", "scheduled", "posting", "uploading", "processing"]);
const PLATS = ["ig", "fb", "li", "yt"];

export function pruneReason(j: Json, nowMs = Date.now()): string | null {
  const o = j.output ?? {};
  if (!o.storage_prefix || o.pruned) return null;
  const plats = PLATS.map((k) => o[k]).filter(Boolean);
  if (plats.some((p: Json) => PENDING.has(p.state))) return null;
  const mins = (s?: string) => (s ? (nowMs - Date.parse(s)) / 60000 : Infinity);
  if (["cancelled", "failed"].includes(j.status)) return mins(j.updated_at) > DAY ? "stopped or failed" : null;
  if (j.status === "done") {
    if (!plats.length) return mins(o.decided_at ?? j.updated_at) > 3 * DAY ? "approved; nothing connected to post to" : null;
    if (plats.every((p: Json) => ["posted", "off", "failed"].includes(p.state))) {
      const posted = plats.filter((p: Json) => p.state === "posted" && p.posted_at).map((p: Json) => Date.parse(p.posted_at));
      const last = posted.length ? Math.max(...posted) : Date.parse(o.decided_at ?? j.updated_at);
      return (nowMs - last) / 60000 > 3 * DAY ? "posted 3 days ago" : null;
    }
  }
  return mins(j.created_at) > 7 * DAY ? "older than 7 days" : null;
}

async function sbList(prefix: string): Promise<string[]> {
  const r = await fetch(`${SB}/storage/v1/object/list/reels`, { method: "POST", headers: H, body: JSON.stringify({ prefix, limit: 100 }) });
  if (!r.ok) throw new Error(`storage list failed (${r.status})`);
  return ((await r.json()) ?? []).map((x: Json) => `${prefix}/${x.name}`);
}
async function sbDelete(paths: string[]) {
  if (!paths.length) return;
  const r = await fetch(`${SB}/storage/v1/object/reels`, { method: "DELETE", headers: H, body: JSON.stringify({ prefixes: paths }) });
  if (!r.ok) throw new Error(`storage delete failed (${r.status})`);
}
const merge = (id: string, patch: Json) => rest("POST", "rpc/merge_reel_output", { p_job: id, p_patch: patch }, "return=minimal");

export async function retentionTick(force = false): Promise<Json> {
  if (!force && new Date().getUTCMinutes() % 10 !== 0) return { skipped: true };       // every 10 minutes is plenty
  const rows: Json[] = await rest("GET", "jobs?select=id,status,created_at,updated_at,output&kind=eq.reel&output->>storage_prefix=not.is.null&output->>pruned=is.null&order=created_at.asc&limit=200");
  const res: Json = { deleted: [], deduped: [] }; let budget = 8;
  const r2 = rows.some((j) => j.output?.store === "r2") ? await r2conf(false) : null;
  for (const j of rows) {
    if (budget <= 0) break;
    const o = j.output; const why = pruneReason(j);
    try {
      if (why) {
        budget--;
        if (o.store === "r2") {
          if (!r2) { res.error = "reels are in R2 but R2 is not set up on the site; nothing deleted"; continue; }
          for (const k of await r2list(r2, `${o.storage_prefix}/`)) if (!k.endsWith("thumb.jpg")) await r2delete(r2, k);
        } else {
          await sbDelete((await sbList(o.storage_prefix)).filter((p) => !p.endsWith("thumb.jpg")));
        }
        await merge(j.id, { pruned: true, pruned_at: new Date().toISOString(), prune_reason: why, play_url: null });
        res.deleted.push(`${j.id.slice(0, 8)}: ${why}`);
      } else if (o.store !== "r2" && o.ig_video && o.video && o.video !== o.ig_video) {
        budget--;                                   // an older reel with two copies: keep only the posting copy
        await sbDelete([o.video]);
        await merge(j.id, { video: o.ig_video, store: "sb" });
        res.deduped.push(j.id.slice(0, 8));
      }
    } catch (e) { res.error = `${j.id.slice(0, 8)}: ${String((e as Error).message ?? e).slice(0, 120)}`; }
  }
  return res;
}
