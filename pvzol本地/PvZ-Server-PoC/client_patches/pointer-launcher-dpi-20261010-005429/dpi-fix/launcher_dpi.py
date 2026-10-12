"""DPI setup for the launcher, before importing CLR or creating native windows.

The executable manifest is the primary DPI declaration. These API fallbacks also
cover source launches and older Windows versions without making startup depend
on a particular scaling percentage or on availability of a recent Win32 API.
"""
from __future__ import annotations

import ctypes
import re
import traceback
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


class WindowsDpiApi:
    """Small injectable Win32 boundary; all HWND/context values are pointers."""

    def __init__(self):
        self.user = self.shcore = None
        try:
            self.user = ctypes.WinDLL("user32", use_last_error=True)
        except (AttributeError, OSError):
            pass
        try:
            self.shcore = ctypes.WinDLL("shcore", use_last_error=True)
        except (AttributeError, OSError):
            pass
        for dll, name, arguments, result in (
            (self.user, "SetProcessDpiAwarenessContext", [ctypes.c_void_p], ctypes.c_int),
            (self.user, "SetProcessDPIAware", [], ctypes.c_int),
            (self.user, "GetThreadDpiAwarenessContext", [], ctypes.c_void_p),
            (self.user, "AreDpiAwarenessContextsEqual", [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            (self.user, "GetAwarenessFromDpiAwarenessContext", [ctypes.c_void_p], ctypes.c_int),
            (self.user, "GetDpiForWindow", [ctypes.c_void_p], ctypes.c_uint),
            (self.shcore, "SetProcessDpiAwareness", [ctypes.c_int], ctypes.c_long),
            (self.shcore, "GetProcessDpiAwareness", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)], ctypes.c_long),
        ):
            function = getattr(dll, name, None)
            if function is not None:
                function.argtypes, function.restype = arguments, result
        # pywebview calls this cached ctypes.windll function directly. Configure
        # that function too, before its Python/CLR renderer is imported.
        try:
            function = ctypes.windll.user32.GetDpiForWindow
            function.argtypes, function.restype = [ctypes.c_void_p], ctypes.c_uint
        except (AttributeError, OSError):
            pass

    def supports(self, name):
        owner = self.shcore if name == "set_shcore" else self.user
        native = {"set_context": "SetProcessDpiAwarenessContext",
                  "set_shcore": "SetProcessDpiAwareness",
                  "set_system": "SetProcessDPIAware",
                  "window_dpi": "GetDpiForWindow"}[name]
        return getattr(owner, native, None) is not None

    def set_context(self, context):
        ctypes.set_last_error(0)
        ok = bool(self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(context)))
        return ok, int(ctypes.get_last_error())

    def set_shcore(self, awareness):
        return int(self.shcore.SetProcessDpiAwareness(awareness))

    def set_system(self):
        ctypes.set_last_error(0)
        ok = bool(self.user.SetProcessDPIAware())
        return ok, int(ctypes.get_last_error())

    def current_mode(self):
        get_context = getattr(self.user, "GetThreadDpiAwarenessContext", None)
        equal = getattr(self.user, "AreDpiAwarenessContextsEqual", None)
        context = get_context() if get_context else None
        if context and equal:
            for value, name in ((-4, "per_monitor_v2"), (-3, "per_monitor"),
                                (-2, "system"), (-1, "unaware"), (-5, "unaware_gdi_scaled")):
                if equal(context, ctypes.c_void_p(value)):
                    return name
        get_awareness = getattr(self.user, "GetAwarenessFromDpiAwarenessContext", None)
        if context and get_awareness:
            return {0: "unaware", 1: "system", 2: "per_monitor"}.get(int(get_awareness(context)))
        get_process = getattr(self.shcore, "GetProcessDpiAwareness", None)
        if get_process:
            awareness = ctypes.c_int()
            if int(get_process(None, ctypes.byref(awareness))) == 0:
                return {0: "unaware", 1: "system", 2: "per_monitor"}.get(awareness.value)
        return None

    def window_dpi(self, hwnd):
        return int(self.user.GetDpiForWindow(ctypes.c_void_p(hwnd)))


def initialize_dpi_awareness(api=None):
    """Return diagnostics and continue startup even if all DPI APIs fail.

ERROR_ACCESS_DENIED is expected after a manifest has already set awareness.
Keep that established mode; do not attempt to overwrite or downgrade it.
"""
    attempts = []
    try:
        api = WindowsDpiApi() if api is None else api
        def current_mode():
            try:
                return api.current_mode()
            except Exception:
                return None

        for method, value, mode in (("set_context", -4, "per_monitor_v2"),
                                    ("set_context", -3, "per_monitor"),
                                    ("set_shcore", 2, "per_monitor"),
                                    ("set_shcore", 1, "system"),
                                    ("set_system", None, "system")):
            try:
                if not api.supports(method):
                    attempts.append({"api": method, "mode": mode, "status": "unavailable"})
                    continue
                if method == "set_shcore":
                    error = api.set_shcore(value)
                    ok = error == 0
                    locked = (error & 0xffffffff) == 0x80070005
                else:
                    ok, error = getattr(api, method)(value) if value is not None else api.set_system()
                    locked = error == 5
                attempts.append({"api": method, "mode": mode,
                                 "status": "set" if ok else "failed", "error": int(error)})
                if ok:
                    return {"ok": True, "mode": current_mode() or mode,
                            "source": method, "attempts": attempts}
                if locked:
                    existing = current_mode()
                    return {"ok": existing in ("per_monitor_v2", "per_monitor", "system"),
                            "mode": existing or "unknown", "source": "existing", "attempts": attempts}
            except Exception as exc:
                attempts.append({"api": method, "mode": mode, "status": "exception",
                                 "detail": safe_exception_detail(exc)})
        return {"ok": False, "mode": current_mode() or "unknown",
                "source": "fallback", "attempts": attempts}
    except Exception as exc:
        return {"ok": False, "mode": "unknown", "source": "fallback",
                "attempts": attempts, "detail": safe_exception_detail(exc)}


def get_window_dpi(hwnd, api=None, default=96):
    """Return the DPI of this HWND, allowing fractional/custom display scales."""
    default = int(default) if isinstance(default, (int, float)) and default > 0 else 96
    try:
        hwnd = int(hwnd)
        if hwnd <= 0:
            return default
        api = WindowsDpiApi() if api is None else api
        if api.supports("window_dpi"):
            dpi = int(api.window_dpi(hwnd))
            if dpi > 0:
                return dpi
    except Exception:
        pass
    return default


def safe_exception_detail(exc, sensitive_values=()):
    """Preserve useful failure details without storing authenticated URLs."""
    if isinstance(exc, BaseException):
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    else:
        detail = str(exc)
    for value in sorted((str(v) for v in sensitive_values if v), key=len, reverse=True):
        detail = detail.replace(value, "[redacted]")

    def scrub_url(match):
        value = match.group(0)
        try:
            parsed = urlsplit(value)
            # Every query value is private here; renderer errors need the path,
            # host and exception, rather than a session's query string.
            query = urlencode([(key, "[redacted]") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)])
            host = parsed.netloc.rsplit("@", 1)[-1]
            return urlunsplit((parsed.scheme, host, parsed.path, query, ""))
        except ValueError:
            return "[redacted URL]"

    detail = re.sub(r"https?://[^\s<>\"']+", scrub_url, detail, flags=re.I)
    detail = re.sub(r"(?i)((?:x-pvz-token|authorization|access_token|api[_-]?key|token)\s*[:=]\s*)(?:bearer\s+)?[^\s,;'\"<>]+",
                    r"\1[redacted]", detail)
    return detail[:12000]


