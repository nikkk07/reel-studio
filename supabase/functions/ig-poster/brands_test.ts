// Per-account posting, against an in-memory stand-in for PostgREST/Storage and the Instagram, Facebook, Pexels and
// Pixabay APIs:  deno test --allow-env supabase/functions/ig-poster/brands_test.ts
import { assert, assertEquals } from "jsr:@std/assert@1";

Deno.env.set("SUPABASE_URL", "http://sb.test"); Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "svc");
Deno.env.set("IG_API", "http://ig.test/v1"); Deno.env.set("IG_REFRESH_URL", "http://ig.test/refresh");
Deno.env.set("FB_API", "http://fb.test/v1"); Deno.env.set("PEXELS_API", "http://pexels.test"); Deno.env.set("PIXABAY_API", "http://pixabay.test");

type Row = Record<string, any>;
const T: Record<string, Row[]> = { settings: [], secrets: [], jobs: [], brands: [] };
const IG_USERS: Record<string, { id: string; username: string }> = {};       // token -> account
const IG_POSTS: { user: string; caption: string; id: string }[] = [];
const CALLS: string[] = [];

function path(row: Row, col: string) {          // output->ig->>state
  const parts = col.split(/->>?/); let v: any = row;
  for (const p of parts) v = v?.[p];
  return v;
}
function match(row: Row, q: URLSearchParams) {
  for (const [k, raw] of q) {
    if (["select", "order", "limit"].includes(k)) continue;
    const v = path(row, k); const [op, ...rest] = raw.split("."); const arg = rest.join(".");
    if (op === "eq" && String(v ?? "") !== arg) return false;
    if (op === "gte" && !(String(v ?? "") >= arg)) return false;
    if (op === "in" && !arg.slice(1, -1).split(",").includes(String(v))) return false;
    if (op === "like" && !new RegExp("^" + arg.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*") + "$").test(String(v ?? ""))) return false;
  }
  return true;
}
const J = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });

globalThis.fetch = async (input: string | URL | Request, init: RequestInit = {}) => {
  const u = new URL(String(input instanceof Request ? input.url : input)); const method = (init.method ?? "GET").toUpperCase();
  const body = init.body ? (typeof init.body === "string" ? init.body : String(init.body)) : "";
  CALLS.push(`${method} ${u.host}${u.pathname}`);
  if (u.host === "sb.test") {
    if (u.pathname.startsWith("/storage/v1/object/sign/")) return J({ signedURL: "http://files.test/x.mp4?token=1" });
    if (u.pathname === "/rest/v1/rpc/set_key_status") {
      const a = JSON.parse(body); const s = T.settings.find((r) => r.key === "studio_keys")!;
      if ((s.value[a.p_name]?.updated_at ?? null) === a.p_saved_at) s.value[a.p_name] = { ...s.value[a.p_name], ...a.p_status };
      return new Response(null, { status: 204 });
    }
    if (u.pathname === "/rest/v1/rpc/set_reel_stats") return new Response(null, { status: 204 });
    const table = u.pathname.replace("/rest/v1/", ""); const rows = T[table]; const q = u.searchParams;
    if (method === "GET") return J(rows.filter((r) => match(r, q)).map((r) => structuredClone(r)));
    if (method === "POST") {                                  // upsert on key
      const row = JSON.parse(body); const i = rows.findIndex((r) => r.key === row.key);
      if (i >= 0) rows[i] = { ...rows[i], ...row }; else rows.push(row);
      return new Response("", { status: 201 });
    }
    if (method === "PATCH") {
      const patch = JSON.parse(body); const hit = rows.filter((r) => match(r, q)); hit.forEach((r) => Object.assign(r, patch));
      return J(hit);
    }
  }
  if (u.host === "ig.test") {
    const tok = u.searchParams.get("access_token") ?? new URLSearchParams(body).get("access_token") ?? "";
    const me = IG_USERS[tok];
    if (!me) return J({ error: { code: 190, message: "Invalid OAuth access token" } }, 400);
    if (u.pathname === "/v1/me") return J({ user_id: me.id, username: me.username, account_type: "BUSINESS" });
    const p = new URLSearchParams(body);
    if (u.pathname === `/v1/${me.id}/media` && method === "POST") return J({ id: `c-${me.id}-${IG_POSTS.length}` });
    if (u.pathname.startsWith("/v1/c-")) return J({ status_code: "FINISHED" });
    if (u.pathname === `/v1/${me.id}/media_publish`) { const id = `m${IG_POSTS.length}`; IG_POSTS.push({ user: me.id, caption: p.get("creation_id") ?? "", id }); return J({ id }); }
    if (u.pathname.startsWith("/v1/m")) return J({ permalink: `https://instagram.com/p/${u.pathname.split("/").pop()}` });
    return J({ error: { message: "unknown" } }, 404);
  }
  if (u.host === "pexels.test") return J({}, init.headers && (init.headers as Row).Authorization === "GOODpexels".padEnd(56, "x") ? 200 : 401);
  if (u.host === "pixabay.test") return J({}, u.searchParams.get("key") === "123-goodpixabaykey0000" ? 200 : 400);
  if (u.host === "fb.test") {
    const tok = u.searchParams.get("access_token") ?? new URLSearchParams(body).get("access_token") ?? "";
    if (tok === "fb-dead") return J({ error: { code: 190, message: "Error validating access token" } }, 400);
    return J({ id: "555", name: "Reel Studio" });
  }
  if (u.host === "site.test") return J({}, 200);
  if (u.host === "down.test") return J({}, 503);
  if (u.host === "bot.test") return J({}, 403);
  return J({ error: "no route " + u.href }, 599);
};

(Deno as any).serve = () => ({ finished: Promise.resolve(), shutdown() {} });     // the module registers its handler on import
const { tick } = await import("./index.ts");
const { keysTick } = await import("./keys.ts");
const { fbCaption } = await import("./facebook.ts");
const { commentary } = await import("./linkedin.ts");
const { checksTick } = await import("./checks.ts");
const st = (key: string) => T.settings.find((r) => r.key === key)?.value ?? {};
const ago = (m: number) => new Date(Date.now() - m * 60e3).toISOString();

function reset() {
  for (const k of Object.keys(T)) T[k] = [];
  for (const k of Object.keys(IG_USERS)) delete IG_USERS[k];
  IG_POSTS.length = 0; CALLS.length = 0;
  T.brands = [{ id: "weone", name: "We One Aviation", links: {} }, { id: "flystar", name: "Flying Star Aviator", links: {} }, { id: "bookmycharter", name: "Book My Charter", links: {} }];
}
const reel = (id: string, brand: string) => ({ id, brand, kind: "reel", status: "done", updated_at: ago(1),
  output: { title: `T ${id}`, video: `${id}/reel.mp4`, ig_video: `${id}/ig.mp4`, ig_caption: `Caption ${id}`, decided_at: ago(20), ig: { state: "scheduled", at: ago(1), attempts: 0 } } });

Deno.test("each account posts with its own Instagram token, never another account's", async () => {
  reset();
  IG_USERS["tok-weone"] = { id: "100", username: "we_one_aviation" };
  IG_USERS["tok-flystar"] = { id: "200", username: "flyingstaraviator" };
  T.secrets.push({ key: "instagram_token", value: "tok-weone", updated_at: ago(60) }, { key: "instagram_token@flystar", value: "tok-flystar", updated_at: ago(60) });
  T.settings.push({ key: "instagram_status", value: { state: "connected", user_id: "100", username: "we_one_aviation", checked_at: ago(1) } },
                  { key: "instagram_status@flystar", value: { state: "checking" } });
  T.jobs.push(reel("w1", "weone"), reel("f1", "flystar"), reel("b1", "bookmycharter"));
  for (let i = 0; i < 3; i++) await tick(T.brands as any);
  const j = (id: string) => T.jobs.find((x) => x.id === id)!.output.ig;
  assertEquals(j("w1").state, "posted"); assertEquals(j("f1").state, "posted");
  assertEquals(st("instagram_status@flystar").state, "connected"); assertEquals(st("instagram_status@flystar").username, "flyingstaraviator");
  assert(IG_POSTS.some((p) => p.user === "100") && IG_POSTS.some((p) => p.user === "200"), "both accounts posted to their own Instagram");
  assertEquals(IG_POSTS.length, 2, "exactly one post each");
  assertEquals(j("b1").state, "scheduled", "Book My Charter (no Instagram) is never posted anywhere");
  assert(j("b1").error?.includes("Waiting for Instagram"));
});

Deno.test("the same Instagram account cannot be connected to two studio accounts", async () => {
  reset();
  IG_USERS["tok-weone"] = { id: "100", username: "we_one_aviation" };
  T.secrets.push({ key: "instagram_token", value: "tok-weone", updated_at: ago(60) }, { key: "instagram_token@flystar", value: "tok-weone", updated_at: ago(1) });
  T.settings.push({ key: "instagram_status", value: { state: "connected", user_id: "100", username: "we_one_aviation", checked_at: ago(1) } },
                  { key: "instagram_status@flystar", value: { state: "checking" } });
  await tick(T.brands as any);
  assertEquals(st("instagram_status@flystar").state, "duplicate");
  assert(String(st("instagram_status@flystar").note).includes("already connected to We One Aviation"));
  assertEquals(T.secrets.find((r) => r.key === "instagram_token@flystar")!.value, null, "the duplicate token is removed");
  assertEquals(st("instagram_status").state, "connected", "We One keeps its connection");
  await tick(T.brands as any);
  assertEquals(st("instagram_status@flystar").state, "duplicate", "the note stays until a new token is saved");
});

Deno.test("an expired token of one account does not touch another account", async () => {
  reset();
  IG_USERS["tok-weone"] = { id: "100", username: "we_one_aviation" };
  T.secrets.push({ key: "instagram_token", value: "tok-weone", updated_at: ago(60) }, { key: "instagram_token@flystar", value: "tok-dead", updated_at: ago(60) });
  T.settings.push({ key: "instagram_status", value: { state: "connected", user_id: "100", username: "we_one_aviation", checked_at: ago(1) } },
                  { key: "instagram_status@flystar", value: { state: "checking" } });
  await tick(T.brands as any);
  assertEquals(st("instagram_status@flystar").state, "expired");
  assert(String(st("instagram_status@flystar").note).includes("this account's Settings"));
  assertEquals(st("instagram_status").state, "connected");
});

Deno.test("studio keys are checked in the cloud; a newer save is never overwritten by an older check", async () => {
  reset();
  const saved = ago(1);
  T.settings.push({ key: "studio_keys", value: { pexels: { state: "checking", hint: "…xxxx", updated_at: saved }, pixabay: { state: "checking", hint: "…bad!", updated_at: saved } } });
  T.secrets.push({ key: "api_pexels", value: "GOODpexels".padEnd(56, "x") }, { key: "api_pixabay", value: "123-wrong" });
  await keysTick();
  assertEquals(st("studio_keys").pexels.state, "ok"); assertEquals(st("studio_keys").pixabay.state, "bad");
  assert(String(st("studio_keys").pixabay.note).includes("Pixabay did not accept"));
  // saved again while a check ran: the old result must not land on the new key
  st("studio_keys").pixabay = { state: "checking", updated_at: ago(0) };
  T.secrets.find((r) => r.key === "api_pixabay")!.value = "123-goodpixabaykey0000";
  await keysTick();
  assertEquals(st("studio_keys").pixabay.state, "ok");
  T.settings.push({ key: "meta_app", value: { state: "checking", app_id: "123456789", updated_at: saved } });
  T.secrets.push({ key: "meta_app_secret", value: "ab".repeat(16) });
  await keysTick();
  assertEquals(st("meta_app").state, "ok"); assertEquals(st("meta_app").name, "Reel Studio");
});

Deno.test("captions name the right account", () => {
  assertEquals(fbCaption("Hello\nFollow @flyingstaraviator for more", "Flying Star Aviator"), "Hello\nFollow Flying Star Aviator.");
  assertEquals(fbCaption("Hi\nFollow @we_one_aviation"), "Hi\nFollow We One Aviation for daily aviation facts.");
  assert(commentary("Hi\nFollow @x", "Legal Group").includes("Follow Legal Group."));
});

Deno.test("Re-check: a stale 'expired' Facebook status heals when the page token works; a dead token stays expired", async () => {
  reset();
  T.secrets.push({ key: "facebook_page_token@bookmycharter", value: "fb-good" }, { key: "facebook_page_token@flystar", value: "fb-dead" });
  T.settings.push({ key: "facebook_status@bookmycharter", value: { state: "expired", page_id: "555", page_name: "Book My Charter", note: "old", recheck_at: ago(0) } },
                  { key: "facebook_status@flystar", value: { state: "connected", page_id: "777", checked_at: ago(1), recheck_at: ago(0) } });
  await checksTick(T.brands as any);
  assertEquals(st("facebook_status@bookmycharter").state, "connected");
  assertEquals(st("facebook_status@bookmycharter").note, undefined, "the old warning is gone");
  assertEquals(st("facebook_status@bookmycharter").recheck_at, undefined, "the Re-check flag is cleared");
  assertEquals(st("facebook_status@flystar").state, "expired");
  assertEquals(st("facebook_status").state, undefined, "We One Aviation (nothing saved) is not touched");
});

Deno.test("routine checks: a not-working Facebook is re-checked after 30 min; a working one is not called every minute", async () => {
  reset();
  T.secrets.push({ key: "facebook_page_token@bookmycharter", value: "fb-good" }, { key: "facebook_page_token", value: "fb-good" });
  T.settings.push({ key: "facebook_status@bookmycharter", value: { state: "expired", page_id: "555", checked_at: ago(45) } },
                  { key: "facebook_status", value: { state: "connected", page_id: "556", checked_at: ago(5) } });
  await checksTick(T.brands as any);
  assertEquals(st("facebook_status@bookmycharter").state, "connected");
  assertEquals(CALLS.filter((c) => c.startsWith("GET fb.test/v1/556")).length, 0, "a fresh connected page is not checked again");
});

Deno.test("website check: online, down, and online behind bot protection", async () => {
  reset();
  T.brands = [{ id: "weone", name: "We One", website: "https://site.test/" }, { id: "flystar", name: "Fly", website: "https://down.test/" },
              { id: "bookmycharter", name: "BMC", website: "https://bot.test/" }, { id: "legalgroup", name: "Legal", website: null }] as any;
  T.settings.push({ key: "website_status@legalgroup", value: { recheck_at: ago(0) } });
  await checksTick(T.brands as any);
  assertEquals(st("website_status").state, "connected");
  assertEquals(st("website_status@flystar").state, "error");
  assert(String(st("website_status@flystar").note).includes("503"));
  assertEquals(st("website_status@bookmycharter").state, "connected", "403 from bot protection is not reported as down");
  assertEquals(st("website_status@legalgroup").state, "not_connected", "no website: the Re-check answers 'not set'");
});

Deno.test("Re-check of an Instagram that was never connected clears the flag", async () => {
  reset();
  T.settings.push({ key: "instagram_status@flystar", value: { recheck_at: ago(0) } });
  await tick(T.brands as any);
  assertEquals(st("instagram_status@flystar").state, "not_connected");
  assertEquals(st("instagram_status@flystar").recheck_at, undefined);
});

Deno.test("a successful Facebook post proves the connection and clears a stale warning", async () => {
  reset();
  const { fbTick } = await import("./facebook.ts");
  T.secrets.push({ key: "facebook_page_token@bookmycharter", value: "fb-good" });
  T.settings.push({ key: "facebook_status@bookmycharter", value: { state: "expired", page_id: "555", note: "old", checked_at: ago(1) } });
  T.jobs.push({ id: "p1", brand: "bookmycharter", kind: "reel", status: "done", updated_at: ago(1),
    output: { title: "T", video: "p1/r.mp4", ig_caption: "c", decided_at: ago(20), fb: { state: "processing", video_id: "v1", finish_sent: true, finished_at: ago(2) } } });
  await fbTick(T.brands as any);
  assertEquals(st("facebook_status@bookmycharter").state, "connected");
});
