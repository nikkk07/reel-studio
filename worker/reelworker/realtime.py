"""Supabase Realtime for the Mac: the database tells the Mac about new work (public.wake, migration 0029) instead of the
Mac asking every few seconds. While the push connection is up, an idle Mac reads almost nothing from Supabase; when it is
down, every loop falls back to its normal polling, so nothing ever depends on it.

A small WebSocket client (RFC 6455, standard library only) speaking the Phoenix channel protocol Realtime uses:
join "realtime:mac" with a postgres_changes subscription for INSERTs on public.wake, a heartbeat every 25 s, reconnect with
back-off. The service key is sent only to Supabase and never logged.
"""
import base64, json, logging, os, socket, ssl, struct, threading, time
from urllib.parse import urlparse, urlencode

log = logging.getLogger("reelworker")


class WSClosed(Exception):
    pass


class WebSocket:
    """Just enough of a WebSocket client for Realtime: text frames, ping/pong, close."""

    def __init__(self, url: str, timeout: float = 15.0):
        u = urlparse(url)
        self.secure = u.scheme == "wss"
        host, port = u.hostname, u.port or (443 if self.secure else 80)
        raw = socket.create_connection((host, port), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if self.secure else raw
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: ReelStudio-Mac/1.0\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise WSClosed("closed during handshake")
            head += chunk
            if len(head) > 16384:
                raise WSClosed("handshake too long")
        head, self.buf = head.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0].decode("latin-1")
        if " 101 " not in status + " ":
            raise WSClosed(f"handshake refused: {status[:80]}")
        self.lock = threading.Lock()

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self.buf)))
            if not chunk:
                raise WSClosed("connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text: str, opcode: int = 0x1):
        data = text.encode() if isinstance(text, str) else text
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.lock:
            self.sock.sendall(head + mask + body)

    def recv(self, timeout: float):
        """The next text message, or None when `timeout` passes first. Answers pings itself."""
        self.sock.settimeout(timeout)
        parts, first_op = [], None
        try:
            while True:
                b1, b2 = self._read(2)
                fin, op, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
                if n == 126:
                    n = struct.unpack("!H", self._read(2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", self._read(8))[0]
                mask = self._read(4) if b2 & 0x80 else None
                data = self._read(n) if n else b""
                if mask:
                    data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
                if op == 0x9:                       # ping
                    self.send(data, 0xA); continue
                if op == 0xA:                       # pong
                    continue
                if op == 0x8:
                    raise WSClosed("closed by the server")
                if op in (0x1, 0x2):
                    first_op, parts = op, [data]
                elif op == 0x0:
                    parts.append(data)
                if fin:
                    return b"".join(parts).decode("utf-8", "replace")
        except (socket.timeout, TimeoutError):
            return None

    def close(self):
        try:
            self.send(b"", 0x8)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass


class Waker:
    """Wakes the Mac's loops when the database says something happened. `wait(kind, timeout)` returns True when an event
    of that kind arrived (or one arrived since the caller last looked), False after `timeout`."""

    HEARTBEAT_S = 25
    RETRY_S = 5

    def __init__(self, url: str, key: str):
        self.url, self.key = url.rstrip("/"), key
        self.connected = False
        self.cond = threading.Condition()
        self.gen = {"job": 0, "ping": 0}
        self.seen = {}                 # (kind, thread) -> generation the thread has seen
        self.events = 0
        self.ws = None

    # ---- loops call these
    def wait(self, kind: str, timeout: float) -> bool:
        me = (kind, threading.get_ident())
        with self.cond:
            start = self.seen.get(me, self.gen[kind])
            if self.gen[kind] == start:
                self.cond.wait(timeout)
            hit = self.gen[kind] != start
            self.seen[me] = self.gen[kind]
            return hit

    def poke(self, kind: str):
        with self.cond:
            self.gen[kind] = self.gen.get(kind, 0) + 1
            self.events += 1
            self.cond.notify_all()

    # ---- the connection
    def socket_url(self) -> str:
        base = self.url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base}/realtime/v1/websocket?{urlencode({'apikey': self.key, 'vsn': '1.0.0'})}"

    def run_once(self, stop: threading.Event):
        """One connection: join, then read until it drops or `stop` is set."""
        self.ws = ws = WebSocket(self.socket_url())
        ref = 1
        ws.send(json.dumps({"topic": "realtime:mac", "event": "phx_join", "ref": str(ref), "join_ref": str(ref), "payload": {
            "config": {"broadcast": {"ack": False, "self": False}, "presence": {"key": ""}, "private": False,
                       "postgres_changes": [{"event": "INSERT", "schema": "public", "table": "wake"}]},
            "access_token": self.key}}))
        joined, last_beat = False, time.time()
        try:
            while not stop.is_set():
                msg = ws.recv(timeout=min(5, self.HEARTBEAT_S))
                if time.time() - last_beat > self.HEARTBEAT_S:
                    ref += 1; last_beat = time.time()
                    ws.send(json.dumps({"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": str(ref)}))
                if msg is None:
                    continue
                try:
                    m = json.loads(msg)
                except ValueError:
                    continue
                ev, topic, payload = m.get("event"), m.get("topic"), m.get("payload") or {}
                if topic == "realtime:mac" and ev == "phx_reply" and not joined:
                    if payload.get("status") != "ok":
                        raise WSClosed(f"join refused: {str(payload.get('response'))[:160]}")
                    joined = True
                elif topic == "realtime:mac" and ev == "system" and payload.get("status") == "ok" and not self.connected:
                    self.connected = True
                    log.info("realtime: listening for new work (no polling while connected)")
                    self.poke("job"); self.poke("ping")          # look once now: anything that came in while connecting
                elif topic == "realtime:mac" and ev == "system" and payload.get("status") == "error":
                    raise WSClosed(f"realtime error: {str(payload.get('message'))[:160]}")
                elif topic == "realtime:mac" and ev == "postgres_changes":
                    rec = ((payload.get("data") or {}).get("record")) or {}
                    kind = rec.get("kind")
                    if kind in ("job", "ping"):
                        self.poke(kind)
                elif ev in ("phx_error", "phx_close") and topic == "realtime:mac":
                    raise WSClosed(f"channel {ev}")
        finally:
            self.connected = False
            ws.close()

    def loop(self, stop: threading.Event):
        delay = self.RETRY_S
        while not stop.is_set():
            try:
                self.run_once(stop)
                delay = self.RETRY_S
            except Exception as e:  # noqa: BLE001
                if self.connected:
                    self.connected = False
                log.info("realtime unavailable (%s); polling as usual, retry in %ss", str(e)[:120], delay)
                self.poke("job"); self.poke("ping")              # the loops look once, then go back to polling
            stop.wait(delay)
            delay = min(delay * 2, 300)
