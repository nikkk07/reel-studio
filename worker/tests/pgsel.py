"""Test helper: PostgREST's select= on fake rows (columns, json paths, aliases), like the real server does it.
    rows = pgsel.project(rows, query)      # "select=id,ig:output->ig,title:output->>title" -> only those keys
A fake row without updated_at gets a new stamp on every read (the real table's trigger changes it on every write)."""
import json, re, time


def _split(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        depth += ch == "("; depth -= ch == ")"
        if ch == "," and depth == 0:
            out.append(cur); cur = ""
        else:
            cur += ch
    return out + ([cur] if cur else [])


def value(row, col):
    v = row
    for p in re.split(r"->>?", col):
        v = v.get(p) if isinstance(v, dict) else None
    if re.search(r"->>[^>]*$", col) and v is not None and not isinstance(v, str):
        v = json.dumps(v) if isinstance(v, (dict, list)) else ("true" if v is True else "false" if v is False else str(v))
    return v


def project(rows, query=""):
    sel = next((p[7:] for p in query.split("&") if p.startswith("select=")), "*")
    if sel in ("", "*"):
        return rows
    out = []
    for r in rows:
        o = {}
        for item in _split(sel):
            alias, col = item.split(":", 1) if ":" in item else (re.split(r"->>?", item)[-1], item)
            o[alias] = value(r, col)
            if alias == "updated_at" and o[alias] is None:
                o[alias] = str(time.time_ns())
        out.append(o)
    return out
