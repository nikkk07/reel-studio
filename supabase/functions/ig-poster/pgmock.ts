// Test helper: the parts of PostgREST the poster uses, for the in-memory databases in the *_test.ts files.
// Filters: eq, neq, gt, gte, lt, in, like, is (null/true/false), the not. prefix, and or=(...) with json paths.
// select: plain columns and json paths, with or without an alias ("ig:output->ig", "output->ig->>state" -> "state").
// deno-lint-ignore-file no-explicit-any
type Row = Record<string, any>;

export function path(row: Row, col: string): any {
  let v: any = row;
  for (const p of col.split(/->>?/)) v = v?.[p];
  if (/->>[^>]*$/.test(col) && v != null && typeof v !== "string") v = typeof v === "object" ? JSON.stringify(v) : String(v);
  return v;
}
// split "a,b(c,d),e" on top-level commas
function split(s: string): string[] {
  const out: string[] = []; let depth = 0, cur = "";
  for (const ch of s) {
    if (ch === "(") depth++; if (ch === ")") depth--;
    if (ch === "," && depth === 0) { out.push(cur); cur = ""; } else cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}
function test(v: any, raw: string): boolean {
  if (raw.startsWith("not.")) return !test(v, raw.slice(4));
  const [op, ...rest] = raw.split("."); const arg = rest.join(".");
  const s = v == null ? null : typeof v === "object" ? JSON.stringify(v) : String(v);
  switch (op) {
    case "eq": return s === arg;
    case "neq": return s !== arg;
    case "gt": return s != null && s > arg;
    case "gte": return s != null && s >= arg;
    case "lt": return s != null && s < arg;
    case "in": return s != null && split(arg.slice(1, -1)).map((x) => x.replace(/^"|"$/g, "")).includes(s);
    case "like": return s != null && new RegExp("^" + arg.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*") + "$").test(s);
    case "is": return arg === "null" ? v == null : String(v) === arg;
    default: throw new Error(`pgmock: unsupported operator ${op}`);
  }
}
// "output->ig->>state.in.(a,b)" -> column + condition (the column ends before the first known operator)
function cond(row: Row, c: string): boolean {
  if (c.startsWith("or(")) return split(c.slice(3, -1)).some((x) => cond(row, x));
  if (c.startsWith("and(")) return split(c.slice(4, -1)).every((x) => cond(row, x));
  const m = c.match(/^(.+?)\.((?:not\.)?(?:eq|neq|gt|gte|lt|in|like|is)\..*)$/);
  if (!m) throw new Error(`pgmock: cannot read condition ${c}`);
  return test(path(row, m[1]), m[2]);
}
export function match(row: Row, q: URLSearchParams): boolean {
  for (const [k, raw] of q) {
    if (["select", "order", "limit", "offset"].includes(k)) continue;
    if (k === "or") { if (!split(raw.slice(1, -1)).some((x) => cond(row, x))) return false; continue; }
    if (k === "and") { if (!split(raw.slice(1, -1)).every((x) => cond(row, x))) return false; continue; }
    if (!test(path(row, k), raw)) return false;
  }
  return true;
}
export function project(row: Row, select: string | null): Row {
  if (!select || select === "*") return structuredClone(row);
  const out: Row = {};
  for (const item of split(select)) {
    const [alias, col] = item.includes(":") ? item.split(":") : [item.split(/->>?/).pop()!, item];
    const v = path(row, col);
    out[alias] = v === undefined ? null : structuredClone(v);
  }
  return out;
}
