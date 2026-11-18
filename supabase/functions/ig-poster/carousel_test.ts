// Carousel posting (Reel Studio 0026), against an in-memory database and a fake Instagram API.
//   SUPABASE_URL=http://db.test deno test --allow-env --allow-net=none carousel_test.ts
import { assert, assertEquals } from "jsr:@std/assert@1";

// deno-lint-ignore no-explicit-any
type J = Record<string, any>;
const past = new Date(Date.now() - 3600e3).toISOString();
const DB: J = {
  jobs: [] as J[],
  settings: { instagram_status: { state: "connected", user_id: "u1", username: "we_one_aviation", checked_at: new Date().toISOString() } } as J,
  secrets: { instagram_token: { value: "IGtoken", updated_at: new Date().toISOString() } } as J,
};
const IG: J = { calls: [] as J[], containers: {} as J, published: [] as string[], fail_slide: 0 };
let n = 0;
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } });

// deno-lint-ignore no-explicit-any
(Deno as any).serve = () => ({});
globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
  const url = new URL(String(input)); const method = init?.method ?? "GET";
  if (url.host === "db.test") {
    const p = url.pathname;
    if (p.startsWith("/storage/v1/object/sign/reels/")) return json({ signedURL: `/object/sign/reels/${p.split("/reels/")[1]}?token=t` });
    const table = p.split("/rest/v1/")[1];
    const q = url.searchParams;
    if (table === "jobs" && method === "GET") {
      const st = q.get("output->ig->>state") ?? "";
      return json(DB.jobs.filter((j: J) => j.status === "done" && (!st || st.includes(j.output?.ig?.state))));
    }
    if (table === "jobs" && method === "PATCH") {
      const id = q.get("id")!.slice(3); const j = DB.jobs.find((x: J) => x.id === id);
      const exp = q.get("output->ig->>state");
      if (!j || (exp && `eq.${j.output?.ig?.state}` !== exp)) return json([]);
      Object.assign(j, JSON.parse(String(init!.body))); return json([j]);
    }
    if (table === "settings" && method === "GET") {
      const k = q.get("key") ?? "";
      if (k.startsWith("like.")) return json(Object.entries(DB.settings).map(([key, value]) => ({ key, value })));
      const v = DB.settings[decodeURIComponent(k.slice(3))]; return json(v ? [{ value: v }] : []);
    }
    if (table === "settings" && method === "POST") { const b = JSON.parse(String(init!.body)); DB.settings[b.key] = b.value; return new Response(null, { status: 201 }); }
    if (table === "secrets" && method === "GET") { const v = DB.secrets[decodeURIComponent((q.get("key") ?? "").slice(3))]; return json(v ? [v] : []); }
    return json([]);
  }
  // the fake Instagram API
  const body = method === "POST" ? Object.fromEntries(new URLSearchParams(String(init!.body))) : Object.fromEntries(url.searchParams);
  IG.calls.push({ method, path: url.pathname, body });
  if (url.pathname.endsWith("/me")) return json({ user_id: "u1", username: "we_one_aviation", account_type: "BUSINESS" });
  if (url.pathname.endsWith("/u1/media") && method === "POST") {
    if (body.is_carousel_item === "true" && IG.fail_slide && IG.calls.filter((c: J) => c.body.is_carousel_item).length === IG.fail_slide)
      return json({ error: { code: 2, message: "temporary problem", is_transient: true } }, 500);
    const id = `c${++n}`; IG.containers[id] = body; return json({ id });
  }
  if (url.pathname.endsWith("/u1/media_publish")) { IG.published.push(body.creation_id); return json({ id: "m1" }); }
  const id = url.pathname.split("/").pop()!;
  if (IG.containers[id]) return json({ status_code: "FINISHED" });
  if (id === "m1") return json({ permalink: "https://www.instagram.com/p/abc/" });
  return json({ error: { code: 100, message: "unknown" } }, 400);
}) as typeof fetch;

const { tick } = await import("./index.ts");
const brand = [{ id: "weone", name: "We One Aviation" }];

Deno.test("CA01 a carousel posts as 3 slide items + 1 CAROUSEL container, then publishes once", async () => {
  DB.jobs.push({ id: "j1", status: "done", output: { format: "carousel", slides: ["j1/slide_01.jpg", "j1/slide_02.jpg", "j1/slide_03.jpg"],
    ig_caption: "CPL ka kharcha, line by line", decided_at: past, ig: { state: "scheduled", at: past, attempts: 0 } } });
  IG.fail_slide = 2;                                   // the 2nd slide fails once: the retry must not repeat slide 1
  await tick(brand);
  let g = DB.jobs[0].output.ig;
  assertEquals(g.state, "scheduled"); assertEquals(g.children.length, 1);
  IG.fail_slide = 0; g.at = past;
  await tick(brand);
  g = DB.jobs[0].output.ig;
  const items = IG.calls.filter((c: J) => c.body.is_carousel_item === "true" && c.method === "POST");
  assertEquals(items.length, 4);                       // 1 + (1 failed) + 2: slide 1 never made twice
  assert(items.every((c: J) => c.body.image_url.startsWith("http://db.test/storage/v1/object/sign/reels/j1/slide_0")));
  const car = IG.calls.find((c: J) => c.body.media_type === "CAROUSEL");
  assertEquals(car.body.children.split(",").length, 3); assertEquals(car.body.caption, "CPL ka kharcha, line by line");
  assertEquals(g.state, "posting"); assert(g.container_id);
  await tick(brand);
  g = DB.jobs[0].output.ig;
  assertEquals(g.state, "posted"); assertEquals(g.permalink, "https://www.instagram.com/p/abc/");
  assertEquals(IG.published.length, 1);
  assert(!IG.calls.some((c: J) => c.body.media_type === "REELS"));
});

Deno.test("CA02 a carousel does not wait for a video file; a reel still does", async () => {
  DB.jobs.length = 0; IG.calls.length = 0;
  const fresh = new Date().toISOString();
  DB.jobs.push({ id: "j2", status: "done", output: { video: "j2/reel.mp4", ig_caption: "x", decided_at: fresh, ig: { state: "scheduled", at: past } } });
  DB.jobs.push({ id: "j3", status: "done", output: { format: "carousel", slides: ["a.jpg", "b.jpg"], ig_caption: "y", decided_at: fresh, ig: { state: "scheduled", at: past } } });
  const r = await tick(brand);
  assertEquals(r.weone.log.j2, "waiting for the Mac to prepare the file");
  assertEquals(r.weone.log.j3, "carousel container created");
});
