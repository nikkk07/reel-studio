// YouTube Shorts posting against in-memory stand-ins for PostgREST/Storage, Google sign-in and the YouTube API:
//   deno test --allow-env supabase/functions/ig-poster/youtube_test.ts
import { assert, assertEquals } from "jsr:@std/assert@1";

Deno.env.set("SUPABASE_URL", "http://sb.test"); Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "svc");
Deno.env.set("YT_API", "http://yt.test/youtube/v3"); Deno.env.set("YT_UPLOAD", "http://yt.test/upload/youtube/v3");
Deno.env.set("GOOGLE_TOKEN_URL", "http://google.test/token");

type Row = Record<string, any>;
const T: Record<string, Row[]> = { settings: [], secrets: [], jobs: [], brands: [] };
const SIZE = 20 * 1024 * 1024;
let REFRESH_OK = true, DROP_NEXT_PART = false, PROCESSED = false, THUMB_OK = true; const THUMBS: string[] = [];
const UP: { sessions: Row[]; received: number; meta?: Row; parts: number; done: boolean } = { sessions: [], received: 0, parts: 0, done: false };

function path(row: Row, col: string) { let v: any = row; for (const p of col.split(/->>?/)) v = v?.[p]; return v; }
function match(row: Row, q: URLSearchParams) {
  for (const [k, raw] of q) {
    if (["select", "order", "limit"].includes(k)) continue;
    const v = path(row, k); const [op, ...rest] = raw.split("."); const arg = rest.join(".");
    if (op === "eq" && String(v ?? "") !== arg) return false;
    if (op === "in" && !arg.slice(1, -1).split(",").includes(String(v))) return false;
    if (op === "like" && !new RegExp("^" + arg.replace(/\*/g, ".*") + "$").test(String(v ?? ""))) return false;
  }
  return true;
}
const J = (b: unknown, status = 200, headers: Record<string, string> = {}) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json", ...headers } });
const hdr = (init: RequestInit, k: string) => new Headers(init.headers).get(k);

globalThis.fetch = async (input: string | URL | Request, init: RequestInit = {}) => {
  const u = new URL(String(input instanceof Request ? input.url : input)); const method = (init.method ?? "GET").toUpperCase();
  if (u.host === "sb.test") {
    if (u.pathname === "/storage/v1/object/reels/j1/yt_thumb.jpg") return new Response(new Uint8Array([255, 216, 255]), { status: 200 });
    if (u.pathname.startsWith("/storage/v1/object/reels/")) {
      const [a, b] = (hdr(init, "range") ?? "").replace("bytes=", "").split("-").map(Number);
      return new Response(new Uint8Array(b - a + 1), { status: 206, headers: { "content-range": `bytes ${a}-${b}/${SIZE}` } });
    }
    const table = u.pathname.replace("/rest/v1/", ""); const rows = T[table]; const q = u.searchParams;
    const body = init.body ? String(init.body) : "";
    if (method === "GET") {
      const sel = q.get("select") ?? "";
      return J(rows.filter((r) => match(r, q)).map((r) => sel.startsWith("lang:") ? { lang: r.profile?.language } : structuredClone(r)));
    }
    if (method === "POST") { const row = JSON.parse(body); const i = rows.findIndex((r) => r.key === row.key); if (i >= 0) rows[i] = { ...rows[i], ...row }; else rows.push(row); return new Response("", { status: 201 }); }
    if (method === "PATCH") { const patch = JSON.parse(body); const hit = rows.filter((r) => match(r, q)); hit.forEach((r) => Object.assign(r, patch)); return J(hit); }
  }
  if (u.host === "google.test") {
    const p = new URLSearchParams(String(init.body));
    if (!REFRESH_OK || p.get("refresh_token") !== "1//good") return J({ error: "invalid_grant" }, 400);
    return J({ access_token: "ya29.tok", expires_in: 3599 });
  }
  if (u.host === "yt.test") {
    if (hdr(init, "authorization") !== "Bearer ya29.tok") return J({ error: { code: 401, message: "Invalid Credentials" } }, 401);
    if (u.pathname === "/upload/youtube/v3/videos" && method === "POST") {
      UP.meta = JSON.parse(String(init.body)); const id = `s${UP.sessions.length}`; UP.sessions.push({ id, size: Number(hdr(init, "x-upload-content-length")) });
      return new Response("", { status: 200, headers: { location: `http://yt.test/upload/session/${id}` } });
    }
    if (u.pathname.startsWith("/upload/session/") && method === "PUT") {
      const cr = hdr(init, "content-range") ?? "";
      const done = () => J({ id: "VID123", status: { uploadStatus: "uploaded" } }, 200);
      const incomplete = () => new Response("", { status: 308, headers: UP.received ? { range: `bytes=0-${UP.received - 1}` } : {} });
      if (cr.startsWith("bytes */")) return UP.done ? done() : incomplete();
      const [a, b] = cr.replace("bytes ", "").split("/")[0].split("-").map(Number);
      assertEquals(a, UP.received, "parts arrive in order, never twice");
      UP.received = b + 1; UP.parts++;
      if (UP.received >= SIZE) UP.done = true;
      if (DROP_NEXT_PART) { DROP_NEXT_PART = false; throw new TypeError("connection reset"); }
      return UP.done ? done() : incomplete();
    }
    if (u.pathname === "/upload/youtube/v3/thumbnails/set") { THUMBS.push(u.searchParams.get("videoId")!); return THUMB_OK ? J({ items: [{}] }) : J({ error: { code: 403, message: "The authenticated user doesn't have permissions to upload and set custom video thumbnails." } }, 403); }
    if (u.pathname === "/youtube/v3/videos") return J({ items: [{ id: "VID123", status: { uploadStatus: PROCESSED ? "processed" : "uploaded" } }] });
    if (u.pathname === "/youtube/v3/channels") return J({ items: [{ id: "UCflystar", snippet: { title: "Flying Star Aviator", customUrl: "@flyingstar" } }] });
  }
  throw new Error("unexpected " + u);
};

const { ytTick, ytCheck, ytDescription, ytTitle } = await import("./yt.ts");
const B = { id: "flystar", name: "Flying Star Aviator", website: "https://www.flystar.co.in" };
function seed() {
  T.brands = [{ id: "flystar", name: "Flying Star Aviator", website: B.website, profile: { language: "hinglish" } }];
  T.settings = [{ key: "youtube_app", value: { client_id: "1-x.apps.googleusercontent.com" } }, { key: "youtube_status@flystar", value: { state: "connected", channel_id: "UCflystar", checked_at: new Date().toISOString() } }];
  T.secrets = [{ key: "youtube_client_secret", value: "GOCSPX-x" }, { key: "youtube_refresh@flystar", value: "1//good" }];
  T.jobs = [{ id: "j1", brand: "flystar", kind: "reel", status: "done", output: { title: "Why <pilots> say Roger", ig_video: "j1/ig.mp4", store: "sb",
    ig_caption: "Roger ka matlab kya hai? ✈️\n\nFollow @flyingstaraviator for more.\n💬 Comment CPL and we'll DM you the details.\n\n#aviation #pilot", decided_at: "2026-10-28T00:00:00Z",
    yt: { state: "scheduled", at: "2026-10-28T00:00:00Z", attempts: 0 } } }];
  Object.assign(UP, { sessions: [], received: 0, parts: 0, done: false, meta: undefined });
  REFRESH_OK = true; DROP_NEXT_PART = false; PROCESSED = false;
}
const yt = () => T.jobs[0].output.yt;
async function runUntil(pred: () => boolean, n = 12) { for (let i = 0; i < n && !pred(); i++) await ytTick([B]); }

Deno.test("title and description fit YouTube: no angle brackets, Instagram-only lines rewritten, #Shorts and the website added", () => {
  seed(); const o = T.jobs[0].output;
  assertEquals(ytTitle(o, B), "Why pilots say Roger");
  const d = ytDescription(o, B);
  assert(!d.includes("@flyingstaraviator") && d.includes("Subscribe to Flying Star Aviator"), "Follow @handle becomes Subscribe");
  assert(!/DM you/.test(d) && d.includes("👉 Details: https://www.flystar.co.in"), "the Instagram DM promise is replaced by the website");
  assert(d.endsWith("#Shorts"));
  assert(ytTitle({ title: "x".repeat(150) }, B).length <= 100);
});

Deno.test("a reel uploads in 8 MB parts, resumes after a lost response, and is posted as a public Short once processed", async () => {
  seed(); DROP_NEXT_PART = true;
  await ytTick([B]);                                     // session + parts; the first part's answer is lost
  assertEquals(UP.sessions.length, 1);
  assertEquals(UP.meta!.status.privacyStatus, "public"); assertEquals(UP.meta!.snippet.defaultAudioLanguage, "hi"); assertEquals(UP.meta!.snippet.categoryId, "27");
  assertEquals(yt().state, "scheduled", "the network error is retried later"); assertEquals(yt().resume, "uploading");
  yt().at = "2000-01-01T00:00:00Z";
  await runUntil(() => yt().state !== "uploading" && yt().state !== "scheduled");
  assertEquals(UP.sessions.length, 1, "the same upload session is resumed, never a second copy");
  assertEquals(UP.parts, 3, "20 MB = 3 parts, none sent twice");
  assertEquals(yt().state, "processing"); assertEquals(yt().video_id, "VID123");
  await ytTick([B]); assertEquals(yt().state, "processing", "waits while YouTube processes");
  PROCESSED = true; await ytTick([B]);
  assertEquals(yt().state, "posted"); assertEquals(yt().permalink, "https://www.youtube.com/shorts/VID123");
});

Deno.test("a revoked Google sign-in pauses the upload and marks the channel expired (no failure)", async () => {
  seed(); REFRESH_OK = false;
  await ytTick([B]);
  assertEquals(yt().state, "scheduled"); assert(String(yt().error).startsWith("Waiting for YouTube"));
  const st = T.settings.find((r) => r.key === "youtube_status@flystar")!.value;
  assertEquals(st.state, "expired"); assert(st.note.includes("Reconnect YouTube"));
  assertEquals(UP.sessions.length, 0);
});

Deno.test("a removed video fails clearly instead of retrying", async () => {
  seed(); T.jobs[0].output.pruned = true;
  await ytTick([B]);
  assertEquals(yt().state, "failed"); assert(yt().error.includes("removed after 3 days"));
});

Deno.test("Re-check reads the channel with a fresh token", async () => {
  seed(); T.settings[1].value = { state: "error", recheck_at: new Date().toISOString() };
  assertEquals(await ytCheck(B), "connected");
  const st = T.settings.find((r) => r.key === "youtube_status@flystar")!.value;
  assertEquals(st.channel_title, "Flying Star Aviator"); assertEquals(st.recheck_at, undefined);
});

Deno.test("an AI thumbnail is set once the Short is processed; a refusal (unverified channel) never blocks the post", async () => {
  seed(); T.jobs[0].output.yt = { state: "processing", video_id: "VID123", uploaded_at: new Date().toISOString(), attempts: 1 };
  T.jobs[0].output.yt_thumb = "j1/yt_thumb.jpg"; PROCESSED = true; THUMBS.length = 0;
  await ytTick([B]);
  assertEquals(yt().state, "posted"); assertEquals(yt().thumb, "set"); assertEquals(THUMBS, ["VID123"]);
  seed(); T.jobs[0].output.yt = { state: "processing", video_id: "VID123", uploaded_at: new Date().toISOString(), attempts: 1 };
  T.jobs[0].output.yt_thumb = "j1/yt_thumb.jpg"; PROCESSED = true; THUMB_OK = false;
  await ytTick([B]);
  assertEquals(yt().state, "posted"); assert(String(yt().thumb).startsWith("refused: The authenticated user"));
  THUMB_OK = true;
});
