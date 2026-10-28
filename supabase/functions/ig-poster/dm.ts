// Comment-to-DM: someone comments the account's keyword under a recent post, the account sends them one private reply
// (Instagram "private replies": 1 message per comment, within 7 days of the comment), plus an optional short public reply.
// Runs every 2 minutes in the cloud, so it works while the Mac is off.
// Safety: a comment is claimed in dm_log (unique comment id) BEFORE the message is sent, so it is never messaged twice;
// at most 30 messages an hour per account; the account's own comments and replies are ignored.
import { type Json, type Brand, now, iso, minsAgo, rest, setting, setSetting, secret, bk } from "./db.ts";

const API = Deno.env.get("IG_API") ?? "https://graph.instagram.com/v26.0";
const HOURLY_CAP = 30;
const PERMISSION = new Set([3, 10, 200, 230]);
const PUBLIC_REPLIES = ["Sent you a DM 📩", "Check your DMs 📩", "Just messaged you 📩"];

class DMError extends Error { constructor(msg: string, public code?: number, public auth = false, public perm = false) { super(msg); } }

async function ig(method: "GET" | "POST", path: string, tok: string, params: Json = {}, body?: Json) {
  const u = `${API}/${path}?${new URLSearchParams({ ...params, access_token: tok })}`;
  let r: Response;
  try { r = await fetch(u, body ? { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : { method }); }
  catch (e) { throw new DMError(`network problem talking to Instagram (${(e as Error).name})`); }
  let j: Json = {}; try { j = await r.json(); } catch { /* */ }
  if (!r.ok || j.error) {
    const e = j.error ?? {}; const msg = String(e.error_user_msg ?? e.message ?? `HTTP ${r.status}`).split(tok).join("***").slice(0, 240);
    throw new DMError(msg, e.code, e.code === 190 || r.status === 401, PERMISSION.has(e.code));
  }
  return j;
}

export function matches(text: string, keywords: string[]): string | null {
  const t = ` ${String(text ?? "").toLowerCase()} `;
  for (const k of keywords) {
    const kw = String(k).trim().toLowerCase();
    if (kw && new RegExp(`(^|[^\\p{L}\\p{N}])${kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^\\p{L}\\p{N}]|$)`, "u").test(t)) return k;
  }
  return null;
}

export function render(msg: string, username?: string): string {
  return String(msg).replace(/\{name\}/g, username ? `@${username}` : "there").slice(0, 950);
}

async function dmBrand(b: Brand): Promise<Json> {
  const DK = bk("dm", b.id); const d = await setting(DK);
  if (!d.on) return { off: true };
  const ist = await setting(bk("instagram_status", b.id)); const [tok, tokAt] = await secret(bk("instagram_token", b.id));
  if (!tok || ist.state !== "connected" || !ist.user_id) {
    if (d.state !== "not_connected") await setSetting(DK, { ...d, state: "not_connected", note: "Connect this account's Instagram first; replies start automatically after that.", checked_at: iso(now()) });
    return { connected: false };
  }
  // refused for a missing permission: wait until the token is replaced (Reconnect) or the settings are saved again
  if (d.state === "needs_permission" && !(tokAt && Date.parse(tokAt) > Date.parse(d.checked_at ?? 0))) return { waiting: "permission" };
  const uid = String(ist.user_id); const own = String(ist.username ?? "").toLowerCase();
  const keywords: string[] = (d.keywords ?? []).filter(Boolean);
  if (!keywords.length || !d.message) return { incomplete: true };
  const media: Json[] = ((await ig("GET", `${uid}/media`, tok, { fields: "id,timestamp", limit: "12" })).data ?? [])
    .filter((m: Json) => minsAgo(String(m.timestamp).replace(/\+0000$/, "Z")) < 7 * 1440);
  if (d.state !== "ready") {           // first run (or after a fix): can this token read comments? then it can reply privately
    try { if (media[0]) await ig("GET", `${media[0].id}/comments`, tok, { fields: "id", limit: "1" });
      await setSetting(DK, { ...d, state: "ready", note: null, checked_at: iso(now()) }); d.state = "ready"; }
    catch (e) {
      const err = e as DMError;
      await setSetting(DK, { ...d, state: err.auth ? "not_connected" : err.perm ? "needs_permission" : "error", checked_at: iso(now()),
        note: err.perm ? "The Instagram token needs the instagram_business_manage_comments permission. Generate it again with that permission ticked, then Reconnect Instagram." : `Instagram: ${err.message}` });
      return { state: "not ready" };
    }
  }
  const hourAgo = iso(new Date(Date.now() - 3600e3));
  let budget = HOURLY_CAP - ((await rest("GET", `dm_log?select=id&brand=eq.${b.id}&created_at=gte.${hourAgo}`)) ?? []).length;
  const out: Json = { sent: 0, media: media.length };
  for (const m of media) {
    if (budget <= 0) { out.capped = true; break; }
    const comments: Json[] = ((await ig("GET", `${m.id}/comments`, tok, { fields: "id,text,timestamp,username,from", limit: "50" })).data ?? [])
      .filter((c: Json) => minsAgo(String(c.timestamp).replace(/\+0000$/, "Z")) < 7 * 1440 - 30);
    const cand: Json[] = comments.map((c: Json): Json => ({ ...c, kw: matches(c.text, keywords), who: String(c.username ?? c.from?.username ?? "") }))
      .filter((c: Json) => c.kw && c.who.toLowerCase() !== own && String(c.from?.id ?? "") !== uid);
    if (!cand.length) continue;
    const done = new Set(((await rest("GET", `dm_log?select=comment_id&comment_id=in.(${cand.map((c) => c.id).join(",")})`)) ?? []).map((r: Json) => r.comment_id));
    for (const c of cand) {
      if (done.has(c.id) || budget <= 0) continue;
      try {                            // claim first: a crash or an overlapping run can never message the same person twice
        await rest("POST", "dm_log", { brand: b.id, comment_id: c.id, media_id: m.id, username: c.who.slice(0, 60), keyword: c.kw, text: String(c.text).slice(0, 200), state: "sending" }, "return=minimal");
      } catch { continue; }
      budget--;
      try {
        await ig("POST", `${uid}/messages`, tok, {}, { recipient: { comment_id: c.id }, message: { text: render(d.message, c.who) } });
        if (d.public_reply !== false) {
          try { await ig("POST", `${c.id}/replies`, tok, { message: PUBLIC_REPLIES[out.sent % PUBLIC_REPLIES.length] }); } catch { /* the DM is what matters */ }
        }
        await rest("PATCH", `dm_log?comment_id=eq.${encodeURIComponent(c.id)}`, { state: "sent", sent_at: iso(now()) }, "return=minimal");
        out.sent++;
      } catch (e) {
        const err = e as DMError;
        if (err.auth || err.perm)      // not this person's fault: release the comment so it is answered once the account is fixed
          await rest("DELETE", `dm_log?comment_id=eq.${encodeURIComponent(c.id)}`, undefined, "return=minimal");
        else await rest("PATCH", `dm_log?comment_id=eq.${encodeURIComponent(c.id)}`, { state: "failed", error: err.message.slice(0, 200) }, "return=minimal");
        if (err.auth || err.perm) {
          await setSetting(DK, { ...d, state: err.auth ? "not_connected" : "needs_permission", checked_at: iso(now()),
            note: err.perm ? "Instagram refused to send the private reply: the token needs the instagram_business_manage_comments permission (and messaging must be allowed for the account). Generate it again with that permission, then Reconnect Instagram." : "Instagram connection expired; reconnect it." });
          return out;
        }
      }
    }
  }
  return out;
}

export async function dmTick(list: Brand[], force = false): Promise<Json> {
  if (!force && new Date().getUTCMinutes() % 2 !== 0) return { skipped: true };
  const res: Json = {};
  for (const b of list) { try { const r = await dmBrand(b); if (!r.off) res[b.id] = r; } catch (e) { res[b.id] = { error: String((e as Error).message ?? e).slice(0, 160) }; } }
  return res;
}
