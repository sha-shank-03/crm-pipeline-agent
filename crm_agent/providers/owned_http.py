"""One owned HTTP child, zero retries, cumulative deadline including reserved cleanup."""
import base64
import math
from pathlib import Path
import subprocess
import sys
import time

from ..java_client import DomainError, ROOT
from ..protocol import encode, strict_json
from .http_worker import GATEWAY_URL, validate_url


class GatewayError(DomainError):
    terminal = True

    def __init__(self, code):
        super().__init__(code, "Gateway request stopped: " + code)


def peak_working_set(process):
    """Query only this owned Windows process handle; unknown on other platforms."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        *[(name, ctypes.c_size_t) for name in
                          ["PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                           "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                           "PagefileUsage", "PeakPagefileUsage"]]]
        value = Counters()
        value.cb = ctypes.sizeof(value)
        query = ctypes.WinDLL("psapi").GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        query.restype = wintypes.BOOL
        if query(int(process._handle), ctypes.byref(value), value.cb):
            return value.PeakWorkingSetSize
    except (OSError, AttributeError, ValueError):
        pass
    return None


class OwnedHttpTransport:
    def __init__(self, *, url=GATEWAY_URL, local_double=False, credential_supplier=None, cancel_file=None):
        validate_url(url, local_double)
        if local_double and credential_supplier is not None:
            raise GatewayError("DOUBLE_CREDENTIAL_FORBIDDEN")
        self.url = url
        self.local_double = local_double
        self.credential_supplier = credential_supplier
        self.cancel_file = Path(cancel_file) if cancel_file else None
        self.records = []

    def cancelled(self):
        return self.cancel_file is not None and self.cancel_file.exists()

    def post(self, body, seconds):
        if type(seconds) not in {int, float} or not math.isfinite(seconds) or seconds <= 0:
            raise GatewayError("INVALID_DEADLINE")
        started = time.monotonic()
        deadline = started + min(20.0, seconds)
        cleanup = min(0.25, seconds / 4)
        io_deadline = deadline - cleanup
        if len(body) > 32768:
            raise GatewayError("REQUEST_SIZE")
        if self.cancelled():
            raise GatewayError("CANCELLED")
        token = None
        if not self.local_double:
            if self.credential_supplier is None:
                raise GatewayError("AUTH")
            token = self.credential_supplier()
            if not isinstance(token, str) or not token:
                raise GatewayError("AUTH")
        packet = encode({"url": self.url, "local_double": self.local_double,
                         "token": token, "body": base64.b64encode(body).decode("ascii"),
                         "deadline": io_deadline})
        token = None
        if time.monotonic() >= io_deadline:
            raise GatewayError("DEADLINE")
        try:
            process = subprocess.Popen([sys.executable, "-m", "crm_agent.providers.http_worker"],
                                       cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, shell=False)
        except OSError:
            raise GatewayError("WORKER_UNAVAILABLE") from None
        failure = None
        reply = None
        first = True
        try:
            while True:
                if self.cancelled():
                    failure = GatewayError("CANCELLED")
                    break
                remaining = io_deadline - time.monotonic()
                if remaining <= 0:
                    failure = GatewayError("DEADLINE")
                    break
                try:
                    stdout, stderr = process.communicate(input=packet if first else None,
                                                         timeout=min(0.02, remaining))
                    packet = b""
                    if process.returncode != 0 or len(stdout) > 100000 or len(stderr) > 4096:
                        failure = GatewayError("WORKER_PROTOCOL")
                    else:
                        reply = strict_json(stdout.decode("utf-8", errors="strict"))
                    break
                except subprocess.TimeoutExpired:
                    first = False
        except (ValueError, OSError, UnicodeError):
            failure = GatewayError("WORKER_PROTOCOL")
        finally:
            if process.poll() is None:
                process.kill()  # Popen-owned handle only; no process-name/PID-tree cleanup.
                try:
                    process.communicate(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    failure = GatewayError("CLEANUP_UNKNOWN")
            record = {"pid": process.pid, "exit_code": process.poll(),
                      "owned_child_exited": process.poll() is not None,
                      "peak_working_set_bytes": peak_working_set(process),
                      "wall_seconds": round(time.monotonic() - started, 6)}
            self.records.append(record)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        if time.monotonic() > deadline and failure is None:
            failure = GatewayError("DEADLINE")
        if failure:
            raise failure
        if not isinstance(reply, dict):
            raise GatewayError("WORKER_PROTOCOL")
        if "error" in reply:
            status = reply.get("status")
            if type(status) is int:
                code = "HTTP_" + str(status)
            else:
                code = reply["error"] if reply["error"] in {
                    "CONTENT_TYPE", "RESPONSE_SIZE", "TRANSPORT_UNKNOWN", "REQUEST_SIZE",
                    "AUTH", "WORKER_PROTOCOL", "REDIRECT_OR_PROTOCOL"} else "WORKER_PROTOCOL"
            raise GatewayError(code)
        try:
            payload = base64.b64decode(reply["payload"], validate=True)
        except (KeyError, TypeError, ValueError):
            raise GatewayError("WORKER_PROTOCOL") from None
        if len(payload) > 65536 or time.monotonic() >= deadline:
            raise GatewayError("RESPONSE_SIZE" if len(payload) > 65536 else "DEADLINE")
        return payload
