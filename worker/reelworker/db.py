"""Tiny Supabase client (PostgREST + RPC + Storage) using the service-role key. No secrets are ever logged."""
import json, time
from datetime import datetime, timezone
import httpx
from . import config


class DBError(RuntimeError):
    pass


class Supabase:
    def __init__(self, url=None, key=None):
        self.url = (url or config.SUPABASE_URL).rstrip("/")
        key = key or config.SUPABASE_SERVICE_KEY
        if not self.url or not key:
            raise DBError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing in .env")
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self.c = httpx.Client(timeout=httpx.Timeout(30, write=600, read=120))   # big reel uploads on slow uplinks

    def _req(self, method, path, retries=3, **kw):
        for i in range(retries):
            try:
                r = self.c.request(method, self.url + path, headers={**self.h, **kw.pop("headers", {})}, **kw)
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

    def insert(self, table, row, upsert=False):
        h = {"Prefer": "return=representation" + (",resolution=merge-duplicates" if upsert else "")}
        return self._req("POST", f"/rest/v1/{table}", json=row, headers=h).json()

    def update(self, table, match: str, values: dict):
        r = self._req("PATCH", f"/rest/v1/{table}?{match}", json=values, headers={"Prefer": "return=representation"})
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
