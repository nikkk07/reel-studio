// Reel Studio – finish "Connect LinkedIn" for one account: the site sends the one-time code from LinkedIn's sign-in page here.
// This function (server side, so the Client Secret never reaches a browser) swaps it for a page token, finds that account's
// Company Page the signed-in member administers, and stores token + page under that account. One page, one account.
// Only the studio owner may call it (checked with their own Supabase sign-in).

const SB = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/$/, "");
const KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const ANON = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const LI_API = Deno.env.get("LI_API") ?? "https://api.linkedin.com/rest";
const LI_OAUTH = Deno.env.get("LI_OAUTH") ?? "https://www.linkedin.com/oauth/v2";
const VERSION = Deno.env.get("LI_VERSION") ?? "202610";
const PAGE = Deno.env.get("LI_PAGE_VANITY") ?? "we-one-aviation";
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
async function li(path: string, tok: string) {
  const r = await fetch(`${LI_API}${path}`, { headers: { Authorization: `Bearer ${tok}`, "LinkedIn-Version": VERSION, "X-Restli-Protocol-Version": "2.0.0" } });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`LinkedIn ${r.status}: ${String(j.message ?? "").slice(0, 160)}`);
  return j;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    // 1. only the owner (their own sign-in, checked by the database)
    const auth = req.headers.get("Authorization") ?? "";
    const own = await fetch(`${SB}/rest/v1/rpc/is_owner`, { method: "POST", headers: { apikey: ANON || KEY, Authorization: auth, "Content-Type": "application/json" }, body: "{}" });
    if (!own.ok || (await own.json()) !== true) return reply({ error: "Only the studio owner can connect LinkedIn." }, 403);
    const { code, redirect_uri, brand: brandIn } = await req.json();
    const brand = String(brandIn ?? "weone");
    const bk = (base: string) => (brand === "weone" ? base : `${base}@${brand}`);
    const acct = (await rest("GET", `brands?select=id,name,links&id=eq.${encodeURIComponent(brand)}`))?.[0];
    if (!acct) return reply({ error: "Unknown account." }, 400);
    const vanityWanted = brand === "weone" ? PAGE : (String(acct.links?.linkedin ?? "").match(/linkedin\.com\/company\/([^/?#]+)/i)?.[1] ?? "").toLowerCase();
    const norm = (x: string) => String(x).toLowerCase().replace(/[^a-z0-9]/g, "");
    if (!code || !redirect_uri) return reply({ error: "Missing code from LinkedIn. Start Connect LinkedIn again." }, 400);
    // 2. code -> token (Client Secret stays on the server)
    const app = (await rest("GET", "settings?select=value&key=eq.linkedin_app"))?.[0]?.value ?? {};
    const sec = (await rest("GET", "secrets?select=value&key=eq.linkedin_client_secret"))?.[0]?.value;
    if (!app.client_id || !sec) return reply({ error: "Save the LinkedIn Client ID and Client Secret first." }, 400);
    const t = await fetch(`${LI_OAUTH}/accessToken`, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "authorization_code", code, client_id: app.client_id, client_secret: sec, redirect_uri }) });
    const tj = await t.json().catch(() => ({}));
    if (!t.ok || !tj.access_token) return reply({ error: `LinkedIn did not accept the sign-in (${tj.error_description ?? tj.error ?? t.status}). Check the Client Secret and the redirect URL.` }, 400);
    const tok: string = tj.access_token;
    // 3. which Company Page can this member post for?
    const acls = await li("/organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED", tok);
    const orgs: string[] = (acls.elements ?? []).map((e: Record<string, string>) => e.organization ?? e.organizationTarget).filter(Boolean);
    if (!orgs.length) return reply({ error: `This LinkedIn account is not an admin of any Company Page. Sign in with an admin of ${acct.name}.` }, 400);
    const cands: { urn: string; name: string; vanity: string }[] = [];
    for (const urn of orgs) {
      const o = await li(`/organizations/${urn.split(":").pop()}`, tok).catch(() => ({}));
      cands.push({ urn, name: o.localizedName ?? urn, vanity: String(o.vanityName ?? "") });
    }
    const pick = cands.find((c) => vanityWanted && c.vanity.toLowerCase() === vanityWanted)
      ?? cands.find((c) => norm(c.name) === norm(acct.name)) ?? cands.find((c) => norm(c.name).includes(norm(acct.name)) || norm(acct.name).includes(norm(c.name)))
      ?? (cands.length === 1 ? cands[0] : null);
    if (!pick) return reply({ error: `This LinkedIn account manages ${cands.map((c) => c.name).join(", ")}, but none looks like ${acct.name}. Add ${acct.name}'s LinkedIn page link in its Settings, then connect again.` }, 400);
    const taken = ((await rest("GET", "settings?select=key,value&key=like.linkedin_status*")) ?? [])
      .find((r: Record<string, any>) => r.key !== bk("linkedin_status") && r.value?.state !== "not_connected" && r.value?.org_urn === pick.urn);
    if (taken) return reply({ error: `${pick.name} is already connected to another account. Each account posts to its own LinkedIn page only.` }, 409);
    // 4. store (token only server side; status visible to members)
    await upsert("secrets", { key: bk("linkedin_token"), value: tok });
    await upsert("secrets", { key: bk("linkedin_refresh"), value: tj.refresh_token ?? null });
    const status = { state: "connected", org_urn: pick.urn, org_name: pick.name, vanity: pick.vanity, scope: tj.scope ?? "",
      expires_at: iso(new Date(Date.now() + Number(tj.expires_in ?? 5184000) * 1000)), checked_at: iso(new Date()), note: null };
    await upsert("settings", { key: bk("linkedin_status"), value: status });
    return reply({ ok: true, org_name: pick.name, vanity: pick.vanity, expires_at: status.expires_at });
  } catch (e) {
    return reply({ error: String((e as Error).message ?? e).slice(0, 300) }, 500);
  }
});
