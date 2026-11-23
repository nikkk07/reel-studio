"""Tiny Supabase client (PostgREST + RPC + Storage) using the service-role key. No secrets are ever logged."""
import json, threading, time
from datetime import datetime, timezone
import httpx
from . import config


class DBError(RuntimeError):
    pass


HEADERS_B = 400          # allowance per response for headers (Supabase counts the whole response leaving the project)


class Supabase:
    def __init__(self, url=None, key=None):
        self.url = (url or config.SUPABASE_URL).rstrip("/")
        key = key or config.SUPABASE_SERVICE_KEY
        if not self.url or not key:
            raise DBError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing in .env")
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self.c = httpx.Client(timeout=httpx.Timeout(30, write=600, read=120))   # big reel uploads on slow uplinks
        # data meter (Supabase Free plan: 5 GB a month leaves the project): bytes received, added to egress_log every few
        # minutes; the answer is the guard level the Mac slows its background checks by (0 normal, 1 saver, 2 strict)
        self.meter = {"bytes": 0, "reqs": 0}
        self.guard = {"level": 0}
        self._mlock = threading.Lock()
        self._meter_off = 0.0
        self.on_job_write = None        # macapi.Mirror.on_write: the Mac's own reel saves update its live view directly

    def _count(self, r):
        try:
            n = int(r.num_bytes_downloaded) or len(r.content or b"")     # on the wire (compressed), as Supabase counts it
        except Exception:  # noqa: BLE001
            n = len(r.content or b"")
        with self._mlock:
            self.meter["bytes"] += HEADERS_B + n
            self.meter["reqs"] += 1

    def flush_meter(self) -> dict:
        """Adds this Mac's count to the shared total; returns the guard level. Never raises."""
        if time.time() < self._meter_off:
            return self.guard
        with self._mlock:
            b, n = self.meter["bytes"], self.meter["reqs"]; self.meter["bytes"] = 0; self.meter["reqs"] = 0
        try:
            r = self.rpc("add_egress", p_part="mac", p_bytes=b, p_reqs=n)
            if isinstance(r, dict) and isinstance(r.get("level"), int):
                self.guard = {**r, "at": time.time()}
        except Exception as e:  # noqa: BLE001
            with self._mlock:
                self.meter["bytes"] += b; self.meter["reqs"] += n
            if "404" in str(e) or "PGRST202" in str(e):
                self._meter_off = time.time() + 3600        # not installed yet: normal level
        return self.guard

    @property
    def slow(self) -> int:
        """How much slower background checks run: 1 normal, 2 saver, 4 strict."""
        return (1, 2, 4)[max(0, min(2, int(self.guard.get("level") or 0)))]

    def _req(self, method, path, retries=3, **kw):
        for i in range(retries):
            try:
                r = self.c.request(method, self.url + path, headers={**self.h, **kw.pop("headers", {})}, **kw)
                self._count(r)
                if r.status_code >= 500 or r.status_code == 429:
                    raise DBError(f"{method} {path.split('?')[0]} -> {r.status_code}")
                if r.status_code >= 400:
                    raise DBError(f"{method} {path.split('?')[0]} -> {r.status_code}: {r.text[:300]}")
                return r
            except (httpx.TransportError, DBError) as e:
                if i == retries - 1 or (isinstance(e, DBError) and "-> 4" in str(e) and "429" not in str(e)):
                    raise
                time.sleep(2 * (i + 1))

    # ---- generic
    def rpc(self, fn, **args):
        r = self._req("POST", f"/rest/v1/rpc/{fn}", json=args)
        return r.json() if r.content else None

    def select(self, table, query=""):
        return self._req("GET", f"/rest/v1/{table}?{query}").json()

    # rows nobody reads back (log lines, agent runs, digests) are written without a reply
    QUIET = ("job_events", "agent_runs")

    def insert(self, table, row, upsert=False, minimal=False):
        minimal = minimal or table in self.QUIET or (table == "settings" and upsert)
        h = {"Prefer": ("return=minimal" if minimal else "return=representation") + (",resolution=merge-duplicates" if upsert else "")}
        r = self._req("POST", f"/rest/v1/{table}", json=row, headers=h)
        return None if minimal else r.json()

    def update(self, table, match: str, values: dict, columns: str = "id"):
        """Changes the matching rows; the reply lists only `columns` of them (id by default): enough to tell whether a
        guarded change happened, never the whole reel back after every progress or trace save."""
        hook = self.on_job_write if table == "jobs" else None
        if hook and columns == "id":
            columns = "id,updated_at"
        sel = "" if "select=" in match else f"&select={columns}"
        r = self._req("PATCH", f"/rest/v1/{table}?{match}{sel}", json=values, headers={"Prefer": "return=representation"})
        if hook:
            try:
                hook(table, match, values, r.json() if r.content else [])
            except Exception:  # noqa: BLE001
                pass
        try:
            return r.json() if r.content else []
        except ValueError:
            return []

    def delete(self, table, match: str):
        self._req("DELETE", f"/rest/v1/{table}?{match}")

    # ---- storage
    def upload(self, path_in_bucket: str, file_path, content_type: str):
        data = open(file_path, "rb").read()
        self._req("POST", f"/storage/v1/object/{config.BUCKET}/{path_in_bucket}", content=data,
                  headers={"Content-Type": content_type, "x-upsert": "true"})
        return path_in_bucket

    def sign(self, path_in_bucket: str, expires: int = 7200) -> str:
        r = self._req("POST", f"/storage/v1/object/sign/{config.BUCKET}/{path_in_bucket}", json={"expiresIn": expires}).json()
        u = r.get("signedURL") or r.get("signedUrl") or ""
        return u if u.startswith("http") else f"{self.url}/storage/v1{u}"

    def download(self, path_in_bucket: str, dest, bucket: str = None) -> None:
        r = self._req("GET", f"/storage/v1/object/{bucket or config.BUCKET}/{path_in_bucket}")
        open(dest, "wb").write(r.content)

    def delete_objects(self, paths):
        if paths:
            self._req("DELETE", f"/storage/v1/object/{config.BUCKET}", json={"prefixes": list(paths)})

    def list_objects(self, prefix=""):
        return self._req("POST", f"/storage/v1/object/list/{config.BUCKET}", json={"prefix": prefix, "limit": 1000}).json()


def now_iso(dt=None):
    """UTC timestamp without '+' (a '+' in a URL query string would be read as a space)."""
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
