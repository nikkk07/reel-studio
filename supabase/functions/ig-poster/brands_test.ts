// Per-account posting, against an in-memory stand-in for PostgREST/Storage and the Instagram, Facebook, Pexels and
// Pixabay APIs:  deno test --allow-env supabase/functions/ig-poster/brands_test.ts
import { assert, assertEquals } from "jsr:@std/assert@1";

Deno.env.set("SUPABASE_URL", "http://sb.test"); Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "svc");
Deno.env.set("IG_API", "http://ig.test/v1"); Deno.env.set("IG_REFRESH_URL", "http://ig.test/refresh");
Deno.env.set("FB_API", "http://fb.test/v1"); Deno.env.set("PEXELS_API", "http://pexels.test"); Deno.env.set("PIXABAY_API", "http://pixabay.test");
Deno.env.set("GROQ_API", "http://groq.test/v1"); Deno.env.set("COVERR_API", "http://coverr.test");

type Row = Record<string, any>;
const T: Record<string, Row[]> = { settings: [], secrets: [], jobs: [], brands: [], dm_log: [], dm_person: [], dm_send: [] };
const DMS: { comment: string; text: string; to?: string; file?: string }[] = [];
const CONVS: Row[] = []; const MSGS: Record<string, Row> = {}; let DM_FAIL = ""; const REPLIES: string[] = []; const REPLY_TEXT: string[] = [];
const IG_COMMENTS: Record<string, Row[]> = {}; const IG_MEDIA: Record<string, Row[]> = {}; let DM_PERM = true;
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
    if (method === "GET" && (q.get("select") ?? "").startsWith("mid:"))      // per-reel DM plans by Instagram media id
      return J(rows.filter((r) => match(r, q)).map((r) => ({ mid: r.output?.ig?.media_id, dm: r.output?.dm })));
    if (method === "GET") return J(rows.filter((r) => match(r, q)).map((r) => structuredClone(r)));
    if (method === "POST" && table === "dm_person") {        // primary key (brand, person), merged like resolution=merge-duplicates
      const row = JSON.parse(body); const i = rows.findIndex((r) => r.brand === row.brand && r.person === row.person);
      if (i >= 0) rows[i] = { ...rows[i], ...row }; else rows.push({ ...row, created_at: new Date().toISOString() });
      return new Response("", { status: 201 });
    }
    if (method === "POST" && table === "dm_send") { rows.push({ ...JSON.parse(body), id: rows.length + 1, created_at: new Date().toISOString() }); return new Response("", { status: 201 }); }
    if (method === "POST" && table === "dm_log") {             // unique comment id, like the real table
      const row = JSON.parse(body);
      if (rows.some((r) => r.comment_id === row.comment_id)) return J({ message: "duplicate key" }, 409);
      rows.push({ ...row, created_at: new Date().toISOString() }); return new Response("", { status: 201 });
    }
    if (method === "POST") {                                  // upsert on key
      const row = JSON.parse(body); const i = rows.findIndex((r) => r.key === row.key);
      if (i >= 0) rows[i] = { ...rows[i], ...row }; else rows.push(row);
      return new Response("", { status: 201 });
    }
    if (method === "DELETE") { const keep = rows.filter((r) => !match(r, q)); T[table] = keep; return new Response(null, { status: 204 }); }
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
    if (u.pathname === `/v1/${me.id}/media` && method === "GET" && IG_MEDIA[me.id]) return J({ data: IG_MEDIA[me.id] });
    if (u.pathname === "/v1/me/conversations" && method === "GET") return J({ data: CONVS });
    if (u.pathname.startsWith("/v1/cv") && method === "GET") { const cv = CONVS.find((c) => c.id === u.pathname.split("/")[2]); return J({ messages: { data: (cv?.msgs ?? []).map((id: string) => ({ id, created_time: MSGS[id].created_time })) } }); }
    if (u.pathname.startsWith("/v1/msg") && method === "GET") return J(MSGS[u.pathname.split("/")[2]]);
    if (u.pathname.endsWith("/comments") && method === "GET") return J({ data: IG_COMMENTS[u.pathname.split("/")[2]] ?? [] });
    if (u.pathname === `/v1/${me.id}/messages` && method === "POST") {
      if (!DM_PERM) return J({ error: { code: 10, message: "Application does not have permission for this action" } }, 403);
      const b = JSON.parse(body);
      if (DM_FAIL) return J({ error: { code: 100, message: DM_FAIL } }, 400);
      DMS.push({ comment: b.recipient.comment_id, to: b.recipient.id, text: b.message.text, file: b.message.attachment?.payload?.url });
      const c = Object.values(IG_COMMENTS).flat().find((x) => x.id === b.recipient.comment_id);
      return J({ recipient_id: b.recipient.id ?? c?.from?.id ?? "x", message_id: "m" });
    }
    if (u.pathname.endsWith("/replies") && method === "POST") { REPLIES.push(u.pathname.split("/")[2]); REPLY_TEXT.push(u.searchParams.get("message") ?? ""); return J({ id: "r" }); }
    const p = new URLSearchParams(body);
    if (u.pathname === `/v1/${me.id}/media` && method === "POST") return J({ id: `c-${me.id}-${IG_POSTS.length}` });
    if (u.pathname.startsWith("/v1/c-")) return J({ status_code: "FINISHED" });
    if (u.pathname === `/v1/${me.id}/media_publish`) { const id = `m${IG_POSTS.length}`; IG_POSTS.push({ user: me.id, caption: p.get("creation_id") ?? "", id }); return J({ id }); }
    if (u.pathname.startsWith("/v1/m")) return J({ permalink: `https://instagram.com/p/${u.pathname.split("/").pop()}` });
    return J({ error: { message: "unknown" } }, 404);
  }
  if (u.host === "pexels.test") return J({}, init.headers && (init.headers as Row).Authorization === "GOODpexels".padEnd(56, "x") ? 200 : 401);
  if (u.host === "groq.test") return J({ data: [] }, new Headers(init.headers).get("authorization") === "Bearer gsk_good" ? 200 : 401);
  if (u.host === "coverr.test") return J({ hits: [] }, new Headers(init.headers).get("authorization") === "Bearer coverr-good-key-0000" ? 200 : 403);
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
const { dmTick, matches, render, classify, phoneOf, DEFAULT_PUBLIC } = await import("./dm.ts");
const { pruneReason } = await import("./retention.ts");
const { presign } = await import("./r2.ts");
const st = (key: string) => T.settings.find((r) => r.key === key)?.value ?? {};
const ago = (m: number) => new Date(Date.now() - m * 60e3).toISOString();

function reset() {
  for (const k of Object.keys(T)) T[k] = [];
  for (const k of Object.keys(IG_USERS)) delete IG_USERS[k];
  IG_POSTS.length = 0; CALLS.length = 0; DMS.length = 0; REPLIES.length = 0; REPLY_TEXT.length = 0; DM_PERM = true; DM_FAIL = "";
  CONVS.length = 0; for (const k of Object.keys(MSGS)) delete MSGS[k];
  for (const k of Object.keys(IG_COMMENTS)) delete IG_COMMENTS[k]; for (const k of Object.keys(IG_MEDIA)) delete IG_MEDIA[k];
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
  // the new services: Groq and Coverr are checked with a free request; ThumbAPI is never test-run (it costs credits)
  Object.assign(st("studio_keys"), { groq: { state: "checking", updated_at: saved }, coverr: { state: "checking", updated_at: saved }, thumbapi: { state: "checking", updated_at: saved } });
  T.secrets.push({ key: "api_groq", value: "gsk_good" }, { key: "api_coverr", value: "coverr-bad" }, { key: "api_thumbapi", value: "thumb-key-000000000" });
  const before = CALLS.length;
  await keysTick();
  assertEquals(st("studio_keys").groq.state, "ok"); assertEquals(st("studio_keys").coverr.state, "bad"); assertEquals(st("studio_keys").thumbapi.state, "ok");
  assert(String(st("studio_keys").coverr.note).includes("coverr.co/developers"));
  assert(!CALLS.slice(before).some((c) => c.includes("thumbapi")), "no paid test call");
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

Deno.test("R2 links are signed exactly like AWS's published example (Signature V4)", async () => {
  const u = await presign({ account_id: "x", bucket: "examplebucket", key_id: "AKIAIOSFODNN7EXAMPLE", secret: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", host: "examplebucket.s3.amazonaws.com" },
    "test.txt", 86400, { now: new Date("2013-05-24T00:00:00Z"), uri: "/test.txt", region: "us-east-1" });
  assertEquals(u.split("X-Amz-Signature=")[1], "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404");
});

Deno.test("retention: videos go 3 days after posting, never while a post is pending; thumbnails and numbers stay", () => {
  const now = Date.parse("2026-11-01T12:00:00Z"); const d = (days: number) => new Date(now - days * 86400e3).toISOString();
  const r = (status: string, o: Row, created = d(2)) => pruneReason({ status, created_at: created, updated_at: d(1.5), output: { storage_prefix: "p", ...o } }, now);
  assertEquals(r("done", { ig: { state: "posted", posted_at: d(4) }, fb: { state: "posted", posted_at: d(3.5) } }), "posted 3 days ago");
  assertEquals(r("done", { ig: { state: "posted", posted_at: d(2) } }), null, "posted 2 days ago: kept");
  assertEquals(r("done", { ig: { state: "posted", posted_at: d(5) }, fb: { state: "scheduled" } }, d(9)), null, "Facebook still to post: kept even after 7 days");
  assertEquals(r("cancelled", {}), "stopped or failed");
  assertEquals(r("review", {}, d(8)), "older than 7 days");
  assertEquals(r("review", {}, d(5)), null, "waiting for review 5 days: kept");
  assertEquals(r("done", { decided_at: d(4) }), "approved; nothing connected to post to");
  assertEquals(r("done", { ig: { state: "posted", posted_at: d(9) }, pruned: true }), null, "already removed");
});

Deno.test("Comment-to-DM: keyword match is a whole word, any case; the name is filled in", () => {
  assertEquals(matches("Quote please!", ["QUOTE"]), "QUOTE");
  assertEquals(matches("quotes are great", ["QUOTE"]), null);
  assertEquals(matches("CPL kaise kare? cpl", ["CPL"]), "CPL");
  assertEquals(render("Hi {name}!", "riya_23"), "Hi @riya_23!");
});

Deno.test("Comment-to-DM: one private reply per matching comment, never twice, never to the account itself", async () => {
  reset();
  IG_USERS["tok-bmc"] = { id: "300", username: "book_my_charter" };
  T.secrets.push({ key: "instagram_token@bookmycharter", value: "tok-bmc", updated_at: ago(60) });
  T.settings.push({ key: "instagram_status@bookmycharter", value: { state: "connected", user_id: "300", username: "book_my_charter", checked_at: ago(1) } },
                  { key: "dm@bookmycharter", value: { on: true, keywords: ["QUOTE"], message: "Hi {name}! Reply with route and date.", state: "checking" } });
  IG_MEDIA["300"] = [{ id: "m1", timestamp: ago(60).replace("Z", "+0000") }, { id: "old", timestamp: ago(9 * 1440).replace("Z", "+0000") }];
  IG_COMMENTS["m1"] = [{ id: "c1", text: "quote", timestamp: ago(5), username: "riya" }, { id: "c2", text: "nice reel", timestamp: ago(4), username: "aman" },
                       { id: "c3", text: "QUOTE pls", timestamp: ago(3), username: "book_my_charter" }];
  IG_COMMENTS["old"] = [{ id: "c9", text: "QUOTE", timestamp: ago(9 * 1440), username: "old" }];
  await dmTick(T.brands as any, true);
  assertEquals(st("dm@bookmycharter").state, "ready");
  assertEquals(DMS.map((x) => x.comment), ["c1"], "only the matching comment from someone else, on a recent post");
  assertEquals(DMS[0].text, "Hi @riya! Reply with route and date.");
  assertEquals(REPLIES, ["c1"], "a short public reply too");
  await dmTick(T.brands as any, true);
  assertEquals(DMS.length, 1, "the next run never messages the same comment again");
  assertEquals(T.dm_log.find((r) => r.comment_id === "c1")!.state, "sent");
});

Deno.test("Comment-to-DM: a missing permission is shown on the account, not retried every minute", async () => {
  reset(); DM_PERM = false;
  IG_USERS["tok-fs"] = { id: "200", username: "flyingstaraviator" };
  T.secrets.push({ key: "instagram_token@flystar", value: "tok-fs", updated_at: ago(60) });
  T.settings.push({ key: "instagram_status@flystar", value: { state: "connected", user_id: "200", username: "flyingstaraviator", checked_at: ago(1) } },
                  { key: "dm@flystar", value: { on: true, keywords: ["CPL"], message: "Hi {name}! CPL details here.", state: "ready" } });
  IG_MEDIA["200"] = [{ id: "m2", timestamp: ago(30).replace("Z", "+0000") }];
  IG_COMMENTS["m2"] = [{ id: "c5", text: "CPL", timestamp: ago(2), username: "dev" }, { id: "c6", text: "cpl", timestamp: ago(1), username: "neha" }];
  await dmTick(T.brands as any, true);
  assertEquals(st("dm@flystar").state, "needs_permission");
  assert(String(st("dm@flystar").note).includes("instagram_business_manage_comments"));
  assertEquals(T.dm_log.length, 0, "the refused comment is released, to be answered once the permission is fixed");
  assertEquals(DMS.length, 0);
  await dmTick(T.brands as any, true);
  assertEquals(st("dm@flystar").state, "needs_permission", "no retry every 2 minutes while nothing changed");
  DM_PERM = true; T.secrets.find((r) => r.key === "instagram_token@flystar")!.updated_at = new Date(Date.now() + 1000).toISOString();
  await dmTick(T.brands as any, true);
  assertEquals(DMS.map((x) => x.comment).sort(), ["c5", "c6"], "after Reconnect both waiting comments get their message");
});

Deno.test("Comment-to-DM per reel: the reel's own keyword gets its own DM and reply; the account keyword still works", async () => {
  reset();
  IG_USERS["tok-fs"] = { id: "500", username: "flyingstar" };
  T.secrets.push({ key: "instagram_token@flystar", value: "tok-fs", updated_at: ago(60) });
  T.settings.push({ key: "instagram_status@flystar", value: { state: "connected", user_id: "500", username: "flyingstar", checked_at: ago(1) } },
                  { key: "dm@flystar", value: { on: true, keywords: ["CPL"], message: "Hi {name}! CPL course details.", state: "ready" } });
  T.jobs.push({ id: "r1", brand: "flystar", kind: "reel", status: "done", output: { ig: { state: "posted", media_id: "m7" },
    dm: { keyword: "MEDICAL", message: "Hi {name}, thank you for your comment.\n\nHere is the medical checklist:\n1. Class 2 first.", reply: "Sent! Check your messages for the checklist." } } });
  IG_MEDIA["500"] = [{ id: "m7", timestamp: ago(30).replace("Z", "+0000") }, { id: "m8", timestamp: ago(40).replace("Z", "+0000") }];
  IG_COMMENTS["m7"] = [{ id: "k1", text: "Medical please", timestamp: ago(5), username: "asha" }, { id: "k2", text: "cpl", timestamp: ago(4), username: "ravi" }];
  IG_COMMENTS["m8"] = [{ id: "k3", text: "MEDICAL", timestamp: ago(3), username: "joe" }];
  await dmTick(T.brands as any, true);
  const by = Object.fromEntries(DMS.map((x) => [x.comment, x.text]));
  assert(by.k1.startsWith("Hi @asha, thank you for your comment.") && by.k1.includes("medical checklist"), "reel keyword: the reel's own DM");
  assertEquals(by.k2, "Hi @ravi! CPL course details.", "account keyword on the same reel: the account DM");
  assert(!("k3" in by), "another reel's keyword on a post without that plan is not matched");
  assert(REPLY_TEXT.includes("Sent! Check your messages for the checklist."), "the reel's own public reply");
  assertEquals(T.dm_log.find((r) => r.comment_id === "k1")!.keyword, "MEDICAL");
});

// ---------- universal Comment-to-DM and the inbox (0027) ----------
function weoneUniversal(extra: Row = {}) {
  reset();
  IG_USERS["tok-w1"] = { id: "100", username: "weoneaviation" };
  T.secrets.push({ key: "instagram_token", value: "tok-w1", updated_at: ago(60) });
  T.settings.push({ key: "instagram_status", value: { state: "connected", user_id: "100", username: "weoneaviation", checked_at: ago(1) } },
    { key: "dm", value: { on: true, mode: "universal", message: "Namaste {name}! CPL details.", state: "ready", inbox: true, ...extra } });
  IG_MEDIA["100"] = [{ id: "ad1", timestamp: ago(90).replace("Z", "+0000") }];
}
const conv = (id: string, msgs: [string, string, string, string, number][]) => {   // [msgId, fromId, username, text, minutesAgo]
  CONVS.push({ id, updated_time: ago(Math.min(...msgs.map((m) => m[4]))).replace("Z", "+0000"), msgs: msgs.sort((a, b) => a[4] - b[4]).map((m) => m[0]) });
  for (const [mid, from, username, message, m] of msgs) MSGS[mid] = { id: mid, created_time: ago(m).replace("Z", "+0000"), from: { id: from, username }, message };
};

Deno.test("Universal: spam and angry comments get nothing; links, promotions and 'fraud' are recognised; phone numbers are read", () => {
  assertEquals(classify("check my profile for followers"), "spam");
  assertEquals(classify("visit www.cheap.xyz"), "spam");
  assertEquals(classify("ye sab fraud hai"), "negative");
  assertEquals(classify("Fees kitni hai? 12th PCM done"), null);
  assertEquals(phoneOf("mera number 98765 43210 hai"), "+919876543210");
  assertEquals(phoneOf("+91-98765-43210"), "+919876543210");
  assertEquals(phoneOf("roll no 1234567890 and 2026"), null, "a number that is not an Indian mobile");
  assertEquals(DEFAULT_PUBLIC[0], "Boarding pass is in your inbox 🎫");
});

Deno.test("Universal: every comment counts; one DM per person, a public reply under every comment; spam, angry and own comments skipped", async () => {
  weoneUniversal();
  IG_COMMENTS["ad1"] = [
    { id: "u1", text: "nice", timestamp: ago(9), username: "riya", from: { id: "900", username: "riya" } },
    { id: "u2", text: "fees?", timestamp: ago(8), username: "riya", from: { id: "900", username: "riya" } },
    { id: "u3", text: "🔥", timestamp: ago(7), username: "aman", from: { id: "901", username: "aman" } },
    { id: "u4", text: "follow back for followers", timestamp: ago(6), username: "bot1", from: { id: "902", username: "bot1" } },
    { id: "u5", text: "fake institute", timestamp: ago(5), username: "angry", from: { id: "903", username: "angry" } },
    { id: "u6", text: "Boarding pass is in your inbox 🎫", timestamp: ago(4), username: "weoneaviation", from: { id: "100", username: "weoneaviation" } }];
  await dmTick(T.brands as any, true);
  assertEquals(DMS.map((x) => x.comment), ["u1", "u3"], "riya once (first comment), aman once");
  assertEquals(DMS[0].text, "Namaste @riya! CPL details.");
  assertEquals(REPLIES, ["u1", "u2", "u3"], "a public reply under each of riya's comments and aman's");
  assert(REPLY_TEXT.every((t) => t.startsWith("Boarding pass is in your inbox")));
  assertEquals(T.dm_log.find((r) => r.comment_id === "u2")!.state, "public");
  assertEquals([T.dm_log.find((r) => r.comment_id === "u4")!.reason, T.dm_log.find((r) => r.comment_id === "u5")!.reason], ["spam", "negative"]);
  assert(!T.dm_log.some((r) => r.comment_id === "u6"), "the account's own comment is never touched");
  assertEquals(T.dm_person.map((r) => r.person).sort(), ["900", "901"]);
  IG_COMMENTS["ad1"].push({ id: "u7", text: "again", timestamp: ago(1), username: "riya", from: { id: "900", username: "riya" } });
  await dmTick(T.brands as any, true);
  assertEquals(DMS.length, 2, "riya's later comment: no second DM");
  assertEquals(REPLIES.at(-1), "u7", "but a public reply");
});

Deno.test("Universal: no DM means no 'check your inbox' reply; the hourly cap holds", async () => {
  weoneUniversal({ cap: 3 });
  IG_COMMENTS["ad1"] = [1, 2, 3, 4, 5].map((i) => ({ id: `x${i}`, text: "hi", timestamp: ago(10 - i), username: `p${i}`, from: { id: `80${i}`, username: `p${i}` } }));
  await dmTick(T.brands as any, true);
  assertEquals(DMS.length + REPLIES.length, 3, "three sends in the hour, no more");
  weoneUniversal(); DM_FAIL = "This message is sent outside of allowed window";
  IG_COMMENTS["ad1"] = [{ id: "y1", text: "hi", timestamp: ago(3), username: "zed", from: { id: "700", username: "zed" } }];
  await dmTick(T.brands as any, true);
  assertEquals(REPLIES.length, 0); assertEquals(T.dm_log[0].state, "failed");
});

Deno.test("Inbox: a new person who messages gets the welcome and the PDF once; a chat the team already runs is left alone", async () => {
  weoneUniversal({ pdf_url: "https://files.test/cpl-roadmap.pdf", pdf_name: "CPL Roadmap PDF" });
  IG_COMMENTS["ad1"] = [];
  conv("cv1", [["msg1", "950", "neha", "Hi, CPL ke baare me batao", 3]]);
  conv("cv2", [["msg2", "100", "weoneaviation", "Hello Sir, as discussed", 30], ["msg3", "951", "oldstudent", "ok thanks", 4]]);
  await dmTick(T.brands as any, true);
  const toNeha = DMS.filter((x) => x.to === "950");
  assertEquals(toNeha.map((x) => x.text ?? x.file), ["Namaste @neha! CPL details.", "https://files.test/cpl-roadmap.pdf"]);
  assert(!DMS.some((x) => x.to === "951"), "the team already talks to this person: no bot message");
  await dmTick(T.brands as any, true);
  assertEquals(DMS.filter((x) => x.to === "950").length, 2, "never sent twice");
});

Deno.test("Inbox: a commenter who replies gets the PDF; a phone number is kept for the CRM with a thank-you", async () => {
  weoneUniversal({ pdf_url: "https://files.test/cpl-roadmap.pdf", pdf_name: "CPL Roadmap PDF", thanks: "Thanks {name}! A counsellor will call you today." });
  IG_COMMENTS["ad1"] = [{ id: "z1", text: "details", timestamp: ago(20), username: "kabir", from: { id: "960", username: "kabir" } }];
  await dmTick(T.brands as any, true);
  assert(DMS[0].text!.includes("Reply here and we'll send you the CPL Roadmap PDF"), "the private reply invites a reply for the PDF");
  conv("cv3", [["msg9", "100", "weoneaviation", DMS[0].text!, 19], ["msg10", "960", "kabir", "PDF please, my no. 98111 22333", 2]]);
  await dmTick(T.brands as any, true);
  const toKabir = DMS.filter((x) => x.to === "960").map((x) => x.text ?? x.file);
  assertEquals(toKabir, ["Here's your CPL Roadmap PDF ✈️", "https://files.test/cpl-roadmap.pdf", "Thanks @kabir! A counsellor will call you today."]);
  const p = T.dm_person.find((r) => r.person === "960")!;
  assertEquals([p.phone, p.lead_state, !!p.pdf_at], ["+919811122333", "pending", true]);
});
