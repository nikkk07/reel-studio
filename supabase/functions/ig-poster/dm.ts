// Comment-to-DM: someone comments a reel's own keyword (or the account's keyword) under a recent post, the account sends them one private reply
// (Instagram "private replies": 1 message per comment, within 7 days of the comment), plus an optional short public reply.
// Universal mode (dm.mode = "universal"): every comment counts, whatever it says; one DM per person (dm_person), a public reply
// under every comment; spam gets nothing, angry or "fraud" comments are left for a person. The inbox is answered too: someone who
// messages the account gets the same message once, then the PDF guide (dm.pdf_url); a commenter who replies to the DM gets the
// PDF (Instagram allows only one text to a commenter until they reply). A phone number in a reply is kept for the CRM.
// Runs every 2 minutes in the cloud, so it works while the Mac is off.
// Safety: a comment is claimed in dm_log (unique comment id) BEFORE the message is sent, so it is never messaged twice;
// at most 30 messages an hour per account; the account's own comments and replies are ignored.
import { type Json, type Brand, now, iso, minsAgo, rest, setting, setSetting, secret, bk, everyMin } from "./db.ts";

const API = Deno.env.get("IG_API") ?? "https://graph.instagram.com/v26.0";
const HOURLY_CAP = 30;                  // keyword mode default; universal mode: dm.cap (at most 200 sends an hour)
const MAX_CAP = 200;
export const DEFAULT_PUBLIC = ["Boarding pass is in your inbox 🎫", "Boarding pass is in your inbox ✈️", "Boarding pass is in your inbox 🛫"];
const SPAM = /(https?:\/\/|www\.|\.(com|in|net|xyz)\b|\bpromot(e|ion)\b|\bcollab\b|\bdm (me|for)\b|\bcheck (my|our) (profile|page|bio)\b|\bfollowers?\b|\bcrypto\b|\bbitcoin\b|\bforex\b|\bgiveaway\b|\bearn (money|daily)\b)/i;
const NEGATIVE = /\b(scam|scammer|fraud|fake|cheat(er|ing)?|loot|worst|bakwas|bekar|dhokha|chor)\b/i;
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

// spam (links, promotions) gets nothing; angry or "fraud" comments are left for a person to answer, never a cheerful bot reply
export function classify(text: string): "spam" | "negative" | null {
  const t = String(text ?? "");
  if (SPAM.test(t)) return "spam";
  if (NEGATIVE.test(t)) return "negative";
  return null;
}
// an Indian mobile number anywhere in a message (+91 / 0 / spaces / dashes allowed), as +91XXXXXXXXXX
export function phoneOf(text: string): string | null {
  const d = String(text ?? "").replace(/[^\d+]/g, " ");
  for (const m of d.matchAll(/(?:\+?91|0)?\s*([6-9](?:\s*\d){9})(?!\s*\d)/g)) {
    const n = m[1].replace(/\s/g, "");
    if (/^[6-9]\d{9}$/.test(n)) return `+91${n}`;
  }
  return null;
}
const pdfInvite = (d: Json) => d.pdf_url ? `\n\n📄 Reply here and we'll send you the ${String(d.pdf_name || "PDF guide")} right in this chat.` : "";
const pubText = (d: Json, n: number) => { const l: string[] = (d.public_replies ?? []).filter((x: unknown) => String(x ?? "").trim()); const a = l.length ? l : DEFAULT_PUBLIC; return a[n % a.length]; };
const pkey = (id?: string, username?: string) => (id ? String(id) : `u:${String(username ?? "").toLowerCase()}`);

async function person(brand: string, id?: string, username?: string): Promise<Json | null> {
  if (id) { const r = await rest("GET", `dm_person?select=*&brand=eq.${brand}&person=eq.${encodeURIComponent(String(id))}`); if (r?.[0]) return r[0]; }
  if (username) { const r = await rest("GET", `dm_person?select=*&brand=eq.${brand}&username=eq.${encodeURIComponent(String(username).toLowerCase())}&order=dm_at.desc.nullslast&limit=1`); if (r?.[0]) return r[0]; }
  return null;
}
const savePerson = (row: Json) => rest("POST", "dm_person", { ...row, username: row.username ? String(row.username).toLowerCase() : row.username, updated_at: iso(now()) },
  "resolution=merge-duplicates,return=minimal");
const sent = (brand: string, kind: string) => rest("POST", "dm_send", { brand, kind }, "return=minimal").catch(() => null);

// Universal: every comment, one DM per person, a public reply under every comment the person can be pointed to their inbox for
async function universalComments(b: Brand, d: Json, tok: string, uid: string, own: string, media: Json[], out: Json, budget: { n: number }) {
  for (const m of media) {
    if (budget.n <= 0) { out.capped = true; return; }
    const comments: Json[] = ((await ig("GET", `${m.id}/comments`, tok, { fields: "id,text,timestamp,username,from", limit: "50" })).data ?? [])
      .filter((c: Json) => minsAgo(String(c.timestamp).replace(/\+0000$/, "Z")) < 7 * 1440 - 30)
      .map((c: Json): Json => ({ ...c, who: String(c.username ?? c.from?.username ?? "") }))
      .filter((c: Json) => c.who && c.who.toLowerCase() !== own && String(c.from?.id ?? "") !== uid);
    if (!comments.length) continue;
    const done = new Set(((await rest("GET", `dm_log?select=comment_id&comment_id=in.(${comments.map((c) => c.id).join(",")})`)) ?? []).map((r: Json) => r.comment_id));
    for (const c of comments.sort((x, y) => String(x.timestamp).localeCompare(String(y.timestamp)))) {
      if (done.has(c.id)) continue;
      if (budget.n <= 0) { out.capped = true; return; }
      const kind = classify(c.text);
      const base = { brand: b.id, comment_id: c.id, media_id: m.id, username: c.who.slice(0, 60), keyword: "*", text: String(c.text ?? "").slice(0, 200), person: pkey(c.from?.id, c.who) };
      if (kind) { try { await rest("POST", "dm_log", { ...base, state: "skipped", reason: kind }, "return=minimal"); } catch { /* */ } out[kind] = (out[kind] ?? 0) + 1; continue; }
      try { await rest("POST", "dm_log", { ...base, state: "sending" }, "return=minimal"); } catch { continue; }   // claimed first: never twice
      const p = await person(b.id, c.from?.id, c.who);
      let reached = !!p?.dm_at, state = "public";
      if (!reached) {
        try {
          const r = await ig("POST", `${uid}/messages`, tok, {}, { recipient: { comment_id: c.id }, message: { text: render(String(d.message) + pdfInvite(d), c.who).slice(0, 990) } });
          budget.n--; await sent(b.id, "private");
          await savePerson({ brand: b.id, person: pkey(r.recipient_id ?? c.from?.id, c.who), igsid: r.recipient_id ?? c.from?.id ?? null, username: c.who, source: "comment", dm_at: iso(now()) });
          reached = true; state = "sent"; out.sent++;
        } catch (e) {
          const err = e as DMError;
          if (err.auth || err.perm) {
            await rest("DELETE", `dm_log?comment_id=eq.${encodeURIComponent(c.id)}`, undefined, "return=minimal");
            await setSetting(bk("dm", b.id), { ...d, state: err.auth ? "not_connected" : "needs_permission", checked_at: iso(now()),
              note: err.perm ? "Instagram refused the private reply: the token needs instagram_business_manage_comments and instagram_business_manage_messages. Generate it again with both, then Reconnect Instagram." : "Instagram connection expired; reconnect it." });
            out.stop = true; return;
          }
          await rest("PATCH", `dm_log?comment_id=eq.${encodeURIComponent(c.id)}`, { state: "failed", error: err.message.slice(0, 200) }, "return=minimal");
          continue;                                // no DM, so no "check your inbox" either
        }
      }
      if (reached && d.public_reply !== false && budget.n > 0) {
        try { await ig("POST", `${c.id}/replies`, tok, { message: pubText(d, out.public ?? 0) }); budget.n--; await sent(b.id, "public"); out.public = (out.public ?? 0) + 1; } catch { /* the DM is what matters */ }
      }
      await rest("PATCH", `dm_log?comment_id=eq.${encodeURIComponent(c.id)}`, { state, sent_at: iso(now()) }, "return=minimal");
    }
  }
}

// The inbox: new messages in the last 24 hours (Instagram allows replies within 24 hours of the person's message)
async function inbox(b: Brand, d: Json, tok: string, uid: string, out: Json, budget: { n: number }) {
  const DK = bk("dm", b.id);
  const since = Math.max(d.inbox_at ? Date.parse(d.inbox_at) - 120e3 : 0, Date.now() - 24 * 3600e3 + 300e3);
  const convs: Json[] = (await ig("GET", "me/conversations", tok, { platform: "instagram", fields: "id,updated_time", limit: "25" })).data ?? [];
  let newest = since; const res = { welcomed: 0, pdf: 0, phones: 0 };
  for (const cv of convs) {
    const t = Date.parse(String(cv.updated_time ?? "").replace(/\+0000$/, "Z"));
    if (!(t > since)) continue;
    if (budget.n <= 0) { out.capped = true; break; }
    const ids: Json[] = ((await ig("GET", String(cv.id), tok, { fields: "messages" })).messages?.data ?? []).slice(0, 8);
    const msgs: Json[] = [];
    for (const m of ids) { try { msgs.push(await ig("GET", String(m.id), tok, { fields: "id,created_time,from,to,message" })); } catch { /* older than Instagram keeps */ } }
    const at = (m: Json) => Date.parse(String(m.created_time ?? "").replace(/\+0000$/, "Z"));
    const theirs = msgs.filter((m) => String(m.from?.id ?? "") !== uid && m.from?.id).sort((x, y) => at(x) - at(y));
    if (!theirs.length) { newest = Math.max(newest, t); continue; }
    const who = theirs[theirs.length - 1].from; const id = String(who.id); const username = String(who.username ?? "");
    const p = await person(b.id, id, username);
    const fresh = theirs.filter((m) => at(m) > Math.max(since, p?.last_in_at ? Date.parse(p.last_in_at) : 0));
    if (!fresh.length) { newest = Math.max(newest, t); continue; }
    const first = at(fresh[0]);
    const row: Json = { brand: b.id, person: p?.person ?? id, igsid: id, username: username || p?.username || null, last_in_at: new Date(at(fresh[fresh.length - 1])).toISOString() };
    const send = async (message: Json, kind: string) => { await ig("POST", `${uid}/messages`, tok, {}, { recipient: { id }, message }); budget.n--; await sent(b.id, kind); };
    try {
      if (!p?.dm_at) {
        // someone wrote first: welcome them once, unless the team is already talking to them in this chat
        const team = msgs.some((m) => String(m.from?.id ?? "") === uid && at(m) < first);
        if (!team) {
          await send({ text: render(String(d.message), username || undefined).slice(0, 990) }, "welcome"); row.dm_at = iso(now()); row.source = p?.source ?? "dm"; res.welcomed++;
          if (d.pdf_url && budget.n > 0) { await send({ attachment: { type: "file", payload: { url: String(d.pdf_url) } } }, "pdf"); row.pdf_at = iso(now()); res.pdf++; }
        }
      } else if (d.pdf_url && !p.pdf_at && budget.n > 0) {
        // a commenter answered the private reply: now the PDF can go into the chat
        await send({ text: `Here's your ${String(d.pdf_name || "PDF guide")} ✈️` }, "pdf_intro");
        await send({ attachment: { type: "file", payload: { url: String(d.pdf_url) } } }, "pdf"); row.pdf_at = iso(now()); res.pdf++;
      }
      const phone = p?.phone ? null : fresh.map((m) => phoneOf(String(m.message ?? ""))).find(Boolean);
      if (phone) {
        row.phone = phone; row.lead_state = d.crm === false ? null : "pending"; res.phones++;
        if (d.thanks && budget.n > 0) await send({ text: render(String(d.thanks), username || undefined).slice(0, 990) }, "thanks");
      }
    } catch (e) {
      const err = e as DMError;
      if (err.auth || err.perm) {
        await setSetting(DK, { ...d, inbox_state: err.perm ? "needs_permission" : "not_connected", checked_at: iso(now()),
          note: err.perm ? "Instagram refused a direct message: the token needs instagram_business_manage_messages. Generate it again with it, then Reconnect Instagram." : "Instagram connection expired; reconnect it." });
        out.inbox = { ...res, stop: true }; return;
      }
      row.note = err.message.slice(0, 200);          // outside the 24-hour window or a deleted chat: not retried
    }
    await savePerson(row);
    newest = Math.max(newest, t);
  }
  out.inbox = res;
  await setSetting(DK, { ...d, inbox_at: new Date(newest).toISOString(), inbox_state: "ready" });
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
  if (d.mode !== "universal" && (!keywords.length || !d.message)) return { incomplete: true };
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
  if (d.mode === "universal") {
    if (!d.message) return { incomplete: true };
    const cap = Math.max(1, Math.min(MAX_CAP, Number(d.cap) || MAX_CAP));
    const hourAgo = iso(new Date(Date.now() - 3600e3));
    const budget = { n: cap - ((await rest("GET", `dm_send?select=id&brand=eq.${b.id}&created_at=gte.${hourAgo}`)) ?? []).length };
    const out: Json = { mode: "universal", sent: 0, media: media.length };
    await universalComments(b, d, tok, uid, own, media, out, budget);
    if (!out.stop && d.inbox !== false) {
      try { await inbox(b, d, tok, uid, out, budget); }
      catch (e) { out.inbox = { error: String((e as Error).message).slice(0, 160) }; }
    }
    return out;
  }
  // each reel's own keyword and DM (CTA Strategist); the account's keyword stays the fallback on every post
  const plans: Record<string, Json> = {};
  if (media.length) {
    const rows: Json[] = (await rest("GET", `jobs?select=mid:output->ig->>media_id,dm:output->dm&kind=eq.reel&brand=eq.${b.id}&output->ig->>media_id=in.(${media.map((m) => m.id).join(",")})`)) ?? [];
    for (const r of rows) if (r.mid && r.dm?.keyword && r.dm?.message) plans[String(r.mid)] = r.dm;
  }
  const hourAgo = iso(new Date(Date.now() - 3600e3));
  let budget = HOURLY_CAP - ((await rest("GET", `dm_log?select=id&brand=eq.${b.id}&created_at=gte.${hourAgo}`)) ?? []).length;
  const out: Json = { sent: 0, media: media.length };
  for (const m of media) {
    if (budget <= 0) { out.capped = true; break; }
    const comments: Json[] = ((await ig("GET", `${m.id}/comments`, tok, { fields: "id,text,timestamp,username,from", limit: "50" })).data ?? [])
      .filter((c: Json) => minsAgo(String(c.timestamp).replace(/\+0000$/, "Z")) < 7 * 1440 - 30);
    const plan = plans[String(m.id)];
    const kws = plan ? [String(plan.keyword), ...keywords.filter((k) => k.toUpperCase() !== String(plan.keyword).toUpperCase())] : keywords;
    const cand: Json[] = comments.map((c: Json): Json => ({ ...c, kw: matches(c.text, kws), who: String(c.username ?? c.from?.username ?? "") }))
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
        const own = plan && String(c.kw).toUpperCase() === String(plan.keyword).toUpperCase();
        await ig("POST", `${uid}/messages`, tok, {}, { recipient: { comment_id: c.id }, message: { text: render(own ? plan.message : d.message, c.who) } });
        if (d.public_reply !== false) {
          const pub = own && plan.reply ? String(plan.reply) : PUBLIC_REPLIES[out.sent % PUBLIC_REPLIES.length];
          try { await ig("POST", `${c.id}/replies`, tok, { message: pub }); } catch { /* the DM is what matters */ }
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
  if (!force && !everyMin(2)) return { skipped: true };          // every 2 min; slower when the data guard is on
  const res: Json = {};
  for (const b of list) { try { const r = await dmBrand(b); if (!r.off) res[b.id] = r; } catch (e) { res[b.id] = { error: String((e as Error).message ?? e).slice(0, 160) }; } }
  return res;
}
