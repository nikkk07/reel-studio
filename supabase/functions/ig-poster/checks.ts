// Connection checks for the account Settings page ("Re-check") and routine background checks.
// Instagram is checked by the poster itself (index.ts); Facebook and LinkedIn here; the website is opened like a visitor would.
import { type Json, type Brand, now, iso, minsAgo, setting, setSetting, bk } from "./db.ts";
import { fbCheck } from "./facebook.ts";
import { liCheck } from "./linkedin.ts";

const BLOCKS_BOTS = new Set([401, 403, 429]);      // a live site behind bot protection: online, it just refuses scripts

export async function webCheck(b: Brand): Promise<string | null> {
  const SK = bk("website_status", b.id); const st = await setting(SK); const asked = !!st.recheck_at; const at = iso(now());
  const url = String(b.website ?? "").trim();
  if (!/^https?:\/\/[^\s]+$/i.test(url)) {
    if (!asked && (st.state ?? "not_connected") === "not_connected") return null;
    await setSetting(SK, { state: "not_connected", checked_at: at });
    return "not_connected";
  }
  if (!asked && st.url === url && minsAgo(st.checked_at) < (st.state === "connected" ? 360 : 30)) return null;
  const t0 = Date.now();
  let v: Json;
  try {
    const r = await fetch(url, { redirect: "follow", headers: { "User-Agent": "Mozilla/5.0 (compatible; ReelStudio-status/1.0)" }, signal: AbortSignal.timeout(12000) });
    await r.body?.cancel();
    const ms = Date.now() - t0;
    v = r.ok ? { state: "connected", code: r.status, ms, url, final_url: r.url }
      : BLOCKS_BOTS.has(r.status) ? { state: "connected", code: r.status, ms, url, note: "Online. It refuses automated visits, which is normal behind bot protection." }
      : { state: "error", code: r.status, ms, url, note: `The website answered with an error (HTTP ${r.status}).` };
  } catch (e) {
    const name = (e as Error).name;
    v = { state: "error", url, note: name === "TimeoutError" ? "The website did not answer within 12 seconds." : `The website could not be reached (${name}).` };
  }
  if (v.state === "error" && !asked && st.state === "connected" && !st.failed_once) {      // one blip never raises a false alarm
    await setSetting(SK, { ...st, failed_once: true, checked_at: at }); return "kept";
  }
  await setSetting(SK, { ...v, checked_at: at });
  return v.state;
}

export async function checksTick(list: Brand[]): Promise<Json> {
  const res: Json = {};
  for (const b of list) {
    const r: Json = {};
    for (const [k, f] of [["fb", fbCheck], ["li", liCheck], ["web", webCheck]] as const) {
      try { const x = await f(b); if (x) r[k] = x; } catch (e) { r[k] = `error: ${String((e as Error).message ?? e).slice(0, 120)}`; }
    }
    if (Object.keys(r).length) res[b.id] = r;
  }
  return res;
}
