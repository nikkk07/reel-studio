// Studio keys pasted on the site (Pexels, Pixabay, Groq, Coverr, ThumbAPI, Meta app) are checked here, in the cloud, so a wrong key shows up at once
// even while the Mac is off. A key is checked when it is saved and again once a day. Keys never leave the server.
import { type Json, iso, now, minsAgo, rest, setting, secret } from "./db.ts";
import { r2conf, r2check } from "./r2.ts";

const PEXELS = Deno.env.get("PEXELS_API") ?? "https://api.pexels.com";
const PIXABAY = Deno.env.get("PIXABAY_API") ?? "https://pixabay.com";
const FB = Deno.env.get("FB_API") ?? "https://graph.facebook.com/v26.0";
const GROQ = Deno.env.get("GROQ_API") ?? "https://api.groq.com/openai/v1";
const COVERR = Deno.env.get("COVERR_API") ?? "https://api.coverr.co";

type Verdict = { state: "ok" | "bad" | "checking"; note: string | null; name?: string };

async function probe(url: string, init: RequestInit = {}): Promise<[number, Json]> {
  try {
    const r = await fetch(url, { ...init, signal: AbortSignal.timeout(15000) });
    let j: Json = {}; try { j = await r.json(); } catch { /* */ }
    return [r.status, j];
  } catch { return [0, {}]; }
}

async function check(name: string, key: string, extra: Json): Promise<Verdict> {
  if (name === "pexels") {
    const [st] = await probe(`${PEXELS}/videos/search?query=airplane&per_page=1`, { headers: { Authorization: key } });
    if (st === 200) return { state: "ok", note: null };
    if (st === 401 || st === 403) return { state: "bad", note: "Pexels did not accept this key. Copy it again from pexels.com/api." };
    if (st === 429) return { state: "ok", note: "Pexels says this key reached its hourly limit; it resets by itself." };
    return { state: "checking", note: `Pexels did not answer (${st || "network"}); trying again.` };
  }
  if (name === "pixabay") {
    const [st] = await probe(`${PIXABAY}/api/videos/?key=${encodeURIComponent(key)}&q=airplane&per_page=3`);
    if (st === 200) return { state: "ok", note: null };
    if (st === 400 || st === 401 || st === 403) return { state: "bad", note: "Pixabay did not accept this key. Copy it again from pixabay.com/api/docs (logged in)." };
    if (st === 429) return { state: "ok", note: "Pixabay says this key reached its limit; it resets by itself." };
    return { state: "checking", note: `Pixabay did not answer (${st || "network"}); trying again.` };
  }
  if (name === "groq") {                 // listing models is free and needs a valid key
    const [st] = await probe(`${GROQ}/models`, { headers: { Authorization: `Bearer ${key}` } });
    if (st === 200) return { state: "ok", note: null };
    if (st === 401 || st === 403) return { state: "bad", note: "Groq did not accept this key. Create a new one at console.groq.com/keys." };
    if (st === 429) return { state: "ok", note: "Groq says the free limit is reached for now; it resets by itself." };
    return { state: "checking", note: `Groq did not answer (${st || "network"}); trying again.` };
  }
  if (name === "coverr") {
    const [st] = await probe(`${COVERR}/videos?page_size=1`, { headers: { Authorization: `Bearer ${key}` } });
    if (st === 200) return { state: "ok", note: null };
    if (st === 401 || st === 403) return { state: "bad", note: "Coverr did not accept this key. Copy it again from coverr.co/developers." };
    if (st === 429) return { state: "ok", note: "Coverr's hourly limit is reached (demo keys: 50 searches an hour); it resets by itself." };
    return { state: "checking", note: `Coverr did not answer (${st || "network"}); trying again.` };
  }
  if (name === "thumbapi")              // every test image costs credits, so the key is proven by its first real thumbnail
    return { state: "ok", note: "Saved. Not test-run (each image costs credits); the first YouTube thumbnail proves it." };
  if (name === "meta") {
    const [st, j] = await probe(`${FB}/${encodeURIComponent(extra.app_id)}?fields=id,name&access_token=${encodeURIComponent(`${extra.app_id}|${key}`)}`);
    if (st === 200 && j.id) return { state: "ok", note: null, name: j.name };
    if (st === 400 || st === 401 || st === 403) return { state: "bad", note: "Meta did not accept this App ID and App Secret together. Copy both again from App settings → Basic." };
    return { state: "checking", note: `Meta did not answer (${st || "network"}); trying again.` };
  }
  return { state: "bad", note: "unknown key" };
}

const due = (v: Json) => v && v.state !== "not_set" && (v.state === "checking" || minsAgo(v.checked_at) > 24 * 60);

export async function keysTick(): Promise<Json> {
  const out: Json = {};
  const keys = await setting("studio_keys");
  for (const name of ["pexels", "pixabay", "groq", "coverr", "thumbapi"]) {
    const v = keys[name]; if (!due(v)) continue;
    const [key] = await secret(`api_${name}`);
    const verdict: Verdict = key ? await check(name, key, {}) : { state: "bad", note: "No key saved." };
    const tries = verdict.state === "checking" ? Number(v.tries ?? 0) + 1 : 0;
    const st = { ...verdict, state: tries >= 10 ? "bad" : verdict.state, tries, checked_at: iso(now()) };
    await rest("POST", "rpc/set_key_status", { p_name: name, p_saved_at: v.updated_at ?? null, p_status: st }, "return=minimal");
    out[name] = st.state;
  }
  const meta = await setting("meta_app");
  if (due(meta) && meta.app_id) {
    const [sec] = await secret("meta_app_secret");
    const verdict: Verdict = sec ? await check("meta", sec, meta) : { state: "bad", note: "No App Secret saved." };
    const fresh = await setting("meta_app");                 // never mark a newer save with an older result
    if (fresh.updated_at === meta.updated_at)
      await rest("POST", "settings", { key: "meta_app", value: { ...fresh, ...verdict, checked_at: iso(now()) }, updated_at: iso(now()) }, "resolution=merge-duplicates,return=minimal");
    out.meta = verdict.state;
  }
  const r2s = await setting("r2");                         // Cloudflare R2: a real list request with the saved keys
  if (due(r2s) && r2s.account_id && r2s.bucket) {
    const c = await r2conf(false);
    const verdict: Json = c ? await r2check(c) : { state: "bad", note: "No R2 keys saved." };
    const fresh = await setting("r2");
    if (fresh.updated_at === r2s.updated_at)
      await rest("POST", "settings", { key: "r2", value: { ...fresh, ...verdict, checked_at: iso(now()) }, updated_at: iso(now()) }, "resolution=merge-duplicates,return=minimal");
    out.r2 = verdict.state;
  }
  return out;
}
