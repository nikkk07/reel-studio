// Reel Studio – "Connect Facebook": swaps the short-lived token from Graph API Explorer for a long-lived one (needs the
// App Secret, so it happens here on the server), then takes the We One Aviation Page's own token, which does not expire.
// Only the page token is stored (server side); the App Secret and user token are used once and never saved.

const SB = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/$/, "");
const KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const ANON = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const FB = Deno.env.get("FB_API") ?? "https://graph.facebook.com/v26.0";
const PAGE_HINT = (Deno.env.get("FB_PAGE_HINT") ?? "we one aviation").toLowerCase();
const H = { apikey: KEY, Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" };
const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type", "Access-Control-Allow-Methods": "POST, OPTIONS" };
const iso = (d: Date) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
const upsert = async (table: string, row: Record<string, unknown>) => {
  const r = await fetch(`${SB}/rest/v1/${table}`, { method: "POST", headers: { ...H, Prefer: "resolution=merge-duplicates,return=minimal" }, body: JSON.stringify({ ...row, updated_at: iso(new Date()) }) });
  if (!r.ok) throw new Error(`db ${table} -> ${r.status}`);
};
async function fb(path: string, params: Record<string, string>) {
  const r = await fetch(`${FB}${path}?${new URLSearchParams(params)}`);
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) {
    let msg = String(j.error?.error_user_msg ?? j.error?.message ?? `HTTP ${r.status}`);
    for (const v of Object.values(params)) if (v && v.length > 12) msg = msg.split(v).join("***");
    throw new Error(msg.replace(/access_token=[^&\s]+/g, "access_token=***").slice(0, 240));
  }
  return j;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const own = await fetch(`${SB}/rest/v1/rpc/is_owner`, { method: "POST", headers: { apikey: ANON || KEY, Authorization: req.headers.get("Authorization") ?? "", "Content-Type": "application/json" }, body: "{}" });
    if (!own.ok || (await own.json()) !== true) return reply({ error: "Only the studio owner can connect Facebook." }, 403);
    const { app_id, app_secret, user_token } = await req.json();
    const id = String(app_id ?? "").trim(), sec = String(app_secret ?? "").trim(), tok = String(user_token ?? "").trim();
    if (!/^\d{6,20}$/.test(id)) return reply({ error: "The App ID is the number in App settings → Basic (not the Instagram app ID)." }, 400);
    if (!/^[a-f0-9]{32}$/i.test(sec)) return reply({ error: "The App Secret is the 32-character code in App settings → Basic (click Show)." }, 400);
    if (tok.length < 50) return reply({ error: "Paste the whole access token from Graph API Explorer." }, 400);
    // 1. short-lived user token -> long-lived user token
    let long: string;
    try { long = (await fb("/oauth/access_token", { grant_type: "fb_exchange_token", client_id: id, client_secret: sec, fb_exchange_token: tok })).access_token; }
    catch (e) { return reply({ error: `Facebook did not accept the token or App Secret: ${(e as Error).message}` }, 400); }
    // 2. the Pages this person manages, with their (non-expiring) page tokens
    let pages: Record<string, any>[] = (await fb("/me/accounts", { fields: "id,name,access_token,tasks", limit: "100", access_token: long })).data ?? [];
    // Pages owned by a Business portfolio (or made from Instagram) are often missing from /me/accounts:
    // read the Page IDs this token was granted, then ask each Page for its token directly.
    let granted: string[] = [], who = "";
    try {
      const d = (await fb("/debug_token", { input_token: long, access_token: `${id}|${sec}` })).data ?? {};
      granted = d.scopes ?? [];
      const ids = new Set<string>();
      for (const g of d.granular_scopes ?? []) if (String(g.scope).startsWith("pages_")) for (const t of g.target_ids ?? []) ids.add(String(t));
      for (const pid of ids) if (!pages.some((p) => String(p.id) === pid)) {
        try { const p = await fb(`/${pid}`, { fields: "id,name,access_token,tasks", access_token: long }); if (p.access_token) pages.push(p); } catch { /* no access to that one */ }
      }
    } catch { /* keep what /me/accounts gave */ }
    try { who = (await fb("/me", { fields: "name", access_token: long })).name ?? ""; } catch { /* */ }
    const need = ["pages_show_list", "pages_read_engagement", "pages_manage_posts"].filter((s) => !granted.includes(s));
    if (granted.length && need.length) return reply({ error: `The token is missing: ${need.join(", ")}. Add them in Graph API Explorer and generate a new token.` }, 400);
    if (!pages.length) {
      return reply({ error: `The Facebook account "${who || "this account"}" has no access to a Page. Log in to developers.facebook.com with the Facebook account that manages the We One Aviation Page (or give this account Full control in Page settings → Page access), then generate a new token and tick the Page.` }, 400);
    }
    const page = pages.find((p) => String(p.name).toLowerCase().replace(/[^a-z0-9]/g, "").includes(PAGE_HINT.replace(/[^a-z0-9]/g, ""))) ?? (pages.length === 1 ? pages[0] : null);
    if (!page) return reply({ error: `Found ${pages.map((p) => p.name).join(", ")}, but not the We One Aviation page.` }, 400);
    if (Array.isArray(page.tasks) && !page.tasks.includes("CREATE_CONTENT")) return reply({ error: "Your Facebook account cannot create content on that Page. Ask a Page admin to give you full control." }, 400);
    await fb(`/${page.id}`, { fields: "id,name", access_token: page.access_token });      // the page token works
    // 3. store only the page token (server side) and the visible status
    await upsert("secrets", { key: "facebook_page_token", value: page.access_token });
    await upsert("settings", { key: "facebook_status", value: { state: "connected", page_id: page.id, page_name: page.name, checked_at: iso(new Date()) } });
    return reply({ ok: true, page_name: page.name });
  } catch (e) {
    return reply({ error: String((e as Error).message ?? e).slice(0, 300) }, 500);
  }
});
