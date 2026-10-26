"""Studio Settings on the Mac: keys saved on the site win over the .env; removed / unreachable cloud values fall back to the .env."""
import sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from reelworker import config, studio

res = []
def ok(c, n): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n)

class DB:
    def __init__(self, keys, cap=None, fail=False): self.keys, self.cap, self.fail, self.n = keys, cap, fail, 0
    def select(self, t, q=""):
        self.n += 1
        if self.fail: raise RuntimeError("offline")
        if t == "secrets": return [{"key": f"api_{k}", "value": v} for k, v in self.keys.items()]
        if t == "settings": return [{"value": {"daily_cap": self.cap}}] if self.cap else [{"value": {}}]
        return []

config.ENV["PEXELS_API_KEY"] = "envpexels"; config.ENV.pop("PIXABAY_API_KEY", None)
studio.bind(DB({"pexels": "cloudpexels", "pixabay": None}, cap=5))
ok(studio.key("PEXELS_API_KEY") == ("cloudpexels", "cloud"), "key saved on the site is used")
ok(studio.key("PIXABAY_API_KEY") == (None, None), "no Pixabay key anywhere -> none")
ok(studio.daily_cap() == 5, "daily limit from the site")
studio.bind(DB({}, cap=None))
ok(studio.key("PEXELS_API_KEY") == ("envpexels", "mac"), "key removed on the site -> the Mac's .env key")
ok(studio.daily_cap() == config.DAILY_REEL_CAP, "no limit set -> the Mac's default")
studio.bind(DB({"pexels": "cloudpexels"}, cap=2)); studio.refresh(force=True)
d = DB({}, fail=True); studio._state["db"] = d; studio._state["at"] = 0
ok(studio.key("PEXELS_API_KEY") == ("cloudpexels", "cloud") and studio.daily_cap() == 2, "database unreachable -> last good values kept")
db = DB({"pexels": "x"}); studio.bind(db); studio.get("PEXELS_API_KEY"); n = db.n
for _ in range(20): studio.get("PEXELS_API_KEY")
ok(db.n == n, "cached: no database call per clip search")
ok(studio.sources() == {"pexels": "cloud", "pixabay": None}, "the Mac reports where each key comes from (never the key)")
print(f"{sum(res)}/{len(res)} studio-settings checks passed")
