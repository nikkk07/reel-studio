// Reel Studio – finish "Connect YouTube" for one account: the site sends the one-time code from Google's sign-in page here.
// Server side (the Client Secret never reaches a browser) it is swapped for a long-lived refresh token, the channel of the
// signed-in Google account is looked up, and both are stored under that account. One channel, one account.
// Only the studio owner may call it (checked with their own Supabase sign-in).

const SB = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/$/, "");
const KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const ANON = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const YT = Deno.env.get("YT_API") ?? "https://www.googleapis.com/youtube/v3";
const TOKEN_URL = Deno.env.get("GOOGLE_TOKEN_URL") ?? "https://oauth2.googleapis.com/token";
const UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload";
const H = { apikey: KEY, Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" };
const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type", "Access-Control-Allow-Methods": "POST, OPTIONS" };
const iso = (d: Date) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });

async function rest(method: string, path: string, body?: unknown, prefer = "return=representation") {
  const r = await fetch(`${SB}/rest/v1/${path}`, { method, headers: { ...H, Prefer: prefer }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) throw new Error(`db ${path.split("?")[0]} -> ${r.status}`);
  const t = await r.text(); return t ? JSON.parse(t) : null;
}
const upsert = (table: string, row: Record<string, unknown>) => rest("POST", table, { ...row, updated_at: iso(new Date()) }, "resolution=merge-duplicates,return=minimal");

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    // 1. only the owner (their own sign-in, checked by the database)
    const auth = req.headers.get("Authorization") ?? "";
    const own = await fetch(`${SB}/rest/v1/rpc/is_owner`, { method: "POST", headers: { apikey: ANON || KEY, Authorization: auth, "Content-Type": "application/json" }, body: "{}" });
    if (!own.ok || (await own.json()) !== true) return reply({ error: "Only the studio owner can connect YouTube." }, 403);
    const { code, redirect_uri, brand: brandIn } = await req.json();
    const brand = String(brandIn ?? "weone");
    const bk = (base: string) => (brand === "weone" ? base : `${base}@${brand}`);
    const acct = (await rest("GET", `brands?select=id,name&id=eq.${encodeURIComponent(brand)}`))?.[0];
    if (!acct) return reply({ error: "Unknown account." }, 400);
    if (!code || !redirect_uri) return reply({ error: "Missing code from Google. Start Connect YouTube again." }, 400);
    // 2. code -> tokens (Client Secret stays on the server)
    const app = (await rest("GET", "settings?select=value&key=eq.youtube_app"))?.[0]?.value ?? {};
    const sec = (await rest("GET", "secrets?select=value&key=eq.youtube_client_secret"))?.[0]?.value;
    if (!app.client_id || !sec) return reply({ error: "Save the YouTube app (Client ID and Client Secret) in Studio Settings first." }, 400);
    const t = await fetch(TOKEN_URL, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "authorization_code", code, client_id: app.client_id, client_secret: sec, redirect_uri }) });
    const tj = await t.json().catch(() => ({}));
    if (!t.ok || !tj.access_token) {
      const why = tj.error === "redirect_uri_mismatch" ? `add ${redirect_uri} to the OAuth client's Authorized redirect URIs`
        : tj.error === "invalid_client" ? "check the Client ID and Client Secret in Studio Settings" : (tj.error_description ?? tj.error ?? t.status);
      return reply({ error: `Google did not accept the sign-in (${why}).` }, 400);
    }
    if (!String(tj.scope ?? "").split(" ").includes(UPLOAD_SCOPE))
      return reply({ error: "Google did not grant permission to upload videos. Connect again and tick \"Manage your YouTube videos\" on Google's screen." }, 400);
    if (!tj.refresh_token)
      return reply({ error: "Google did not return a lasting sign-in. Remove the app's access at myaccount.google.com/permissions, then connect again." }, 400);
    // 3. which channel did the owner sign in with?
    const c = await fetch(`${YT}/channels?part=snippet&mine=true`, { headers: { Authorization: `Bearer ${tj.access_token}` } });
    const cj = await c.json().catch(() => ({}));
    if (!c.ok) return reply({ error: `YouTube did not answer (${String(cj.error?.message ?? c.status).replace(/<[^>]+>/g, "").slice(0, 160)}).` }, 400);
    const ch = cj.items?.[0];
    if (!ch) return reply({ error: `This Google account has no YouTube channel. Create ${acct.name}'s channel on youtube.com (or pick the right account/brand channel on Google's screen), then connect again.` }, 400);
    const taken = ((await rest("GET", "settings?select=key,value&key=like.youtube_status*")) ?? [])
      .find((r: Record<string, any>) => r.key !== bk("youtube_status") && r.value?.state !== "not_connected" && r.value?.channel_id === ch.id);
    if (taken) return reply({ error: `${ch.snippet?.title ?? "That channel"} is already connected to another account. Each account uploads to its own channel only.` }, 409);
    // 4. store (token only server side; status visible to members)
    await upsert("secrets", { key: bk("youtube_refresh"), value: tj.refresh_token });
    const status = { state: "connected", channel_id: ch.id, channel_title: ch.snippet?.title ?? "", handle: ch.snippet?.customUrl ?? "",
      thumb: ch.snippet?.thumbnails?.default?.url ?? null, checked_at: iso(new Date()), note: null };
    await upsert("settings", { key: bk("youtube_status"), value: status });
    return reply({ ok: true, channel_title: status.channel_title, handle: status.handle });
  } catch (e) {
    return reply({ error: String((e as Error).message ?? e).slice(0, 300) }, 500);
  }
});
