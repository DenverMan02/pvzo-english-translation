"""Activity heartbeat shared with the desktop launcher (standard library only).

The launcher passes ``PVZ_LAUNCHER_STATUS_FILE`` to every service process it
starts.  The process periodically replaces that small JSON file with counters
it can observe itself: listening ports, open game connections, request totals
and the time of the latest activity.  Nothing here touches players.db.  When
the variable is absent (manual runs) every call is a cheap no-op, so the game
server behaves exactly as before.
"""

import json
import os
import threading
import time

ENV_NAME = "PVZ_LAUNCHER_STATUS_FILE"
WRITE_INTERVAL = 1.0      # seconds between writes while counters change
HEARTBEAT_INTERVAL = 3.0  # rewrite at least this often so freshness is observable


class RuntimeStatus:
    def __init__(self, path, role):
        self.path = path
        self.role = role
        self.enabled = bool(path)
        self.lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.started = time.time()
        self.components = {}
        self._dirty = True
        self._last_write = 0.0
        self._thread = None
        self._stop = threading.Event()

    @classmethod
    def from_env(cls, role):
        return cls(os.environ.get(ENV_NAME, ""), role)

    # -- counters ---------------------------------------------------------
    def _component(self, name):
        value = self.components.get(name)
        if value is None:
            value = self.components[name] = {"active": 0, "total": 0, "last": 0.0, "ports": []}
        return value

    def begin(self, name):
        if not self.enabled:
            return
        with self.lock:
            value = self._component(name)
            value["active"] += 1
            value["total"] += 1
            value["last"] = time.time()
            self._dirty = True

    def end(self, name):
        if not self.enabled:
            return
        with self.lock:
            value = self._component(name)
            value["active"] = max(0, value["active"] - 1)
            value["last"] = time.time()
            self._dirty = True

    def touch(self, name, count=False):
        if not self.enabled:
            return
        with self.lock:
            value = self._component(name)
            if count:
                value["total"] += 1
            value["last"] = time.time()
            self._dirty = True

    def listening(self, name, port):
        if not self.enabled:
            return
        with self.lock:
            ports = self._component(name)["ports"]
            if int(port) not in ports:
                ports.append(int(port))
            self._dirty = True
            started = self._thread is not None
        if started:
            self.write()  # readiness is visible to the launcher right away

    # -- publication --------------------------------------------------------
    def snapshot(self):
        with self.lock:
            return {"role": self.role, "pid": os.getpid(), "started": self.started,
                    "updated": time.time(),
                    "components": {key: dict(value, ports=list(value["ports"]))
                                   for key, value in self.components.items()}}

    def write(self):
        if not self.enabled:
            return False
        # listening() can publish while the heartbeat thread is writing. Keep
        # the shared temporary file and snapshot ordered under a separate lock;
        # snapshot() and the counters below still acquire self.lock themselves.
        with self._write_lock:
            data = self.snapshot()
            temporary = "%s.%d.tmp" % (self.path, os.getpid())
            try:
                directory = os.path.dirname(self.path)
                if directory:
                    os.makedirs(directory, exist_ok=True)
                with open(temporary, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, separators=(",", ":"))
                os.replace(temporary, self.path)
                with self.lock:
                    self._dirty = False
                    self._last_write = time.time()
                return True
            except OSError:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                return False

    def _loop(self):
        while not self._stop.wait(WRITE_INTERVAL):
            with self.lock:
                due = self._dirty or time.time() - self._last_write >= HEARTBEAT_INTERVAL
            if due:
                self.write()

    def start(self):
        if not self.enabled or self._thread is not None:
            return self
        self.write()
        self._thread = threading.Thread(target=self._loop, name="runtime-status", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    # -- integration helpers -----------------------------------------------
    def track_http(self, handler_class, name, ignore_paths=()):
        """Count HTTP responses of a BaseHTTPRequestHandler subclass.

        Every response passes through send_response (send_error included), so
        this observes real requests without changing their handling.
        """
        if not self.enabled or getattr(handler_class, "_pvz_status_tracked", False):
            return
        with self.lock:
            self._component(name)  # "0 requests so far" is known, not "unknown"
            self._dirty = True
        original = handler_class.send_response
        status = self

        def send_response(handler, *args, **kwargs):
            path = str(getattr(handler, "path", "") or "").split("?", 1)[0]
            if path not in ignore_paths:
                status.touch(name, count=True)
            return original(handler, *args, **kwargs)

        handler_class.send_response = send_response
        handler_class._pvz_status_tracked = True


class ObservedReader:
    """Proxy for asyncio.StreamReader that records reads as activity.

    A connection only counts once it has sent data, so bare TCP port probes
    (the launcher's readiness checks) never show up as player activity.
    With ``session=True`` the first data marks the connection active until
    :meth:`close`; otherwise the first data is counted as one request.
    """

    def __init__(self, reader, status, name, session=False):
        self._reader = reader
        self._status = status
        self._name = name
        self._session = session
        self._seen = False

    async def read(self, n=-1):
        data = await self._reader.read(n)
        if data:
            if not self._seen:
                self._seen = True
                if self._session:
                    self._status.begin(self._name)
                else:
                    self._status.touch(self._name, count=True)
            else:
                self._status.touch(self._name)
        return data

    def close(self):
        if self._seen and self._session:
            self._seen = False
            self._status.end(self._name)

    def __getattr__(self, key):
        return getattr(self._reader, key)
