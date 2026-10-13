"""Owned, bounded GET-only Salesforce HTTP transport. External execution is explicitly gated."""
import base64
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from crm_agent.protocol import ProtocolError, encode, strict_json
from crm_agent.providers.owned_http import peak_working_set
from .salesforce import HttpReply, SalesforceError, allowed_url, profile

ROOT = Path(__file__).resolve().parents[2]


def local_base(value):
    return isinstance(value, str) and re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", value) is not None and int(value.rsplit(":",1)[1]) <= 65535


class SalesforceHttp:
    def __init__(self, config, *, credential_supplier=None, allow_external=False, local_base_url=None):
        self.config = profile(config)
        self.local_base_url = local_base_url
        self.mode = "local-http-double" if local_base_url is not None else "external-salesforce"
        if self.mode == "local-http-double":
            if not local_base(local_base_url) or credential_supplier is not None or allow_external:
                raise SalesforceError("INVALID_TRANSPORT")
        elif not allow_external or not self.config["external_enabled"] or credential_supplier is None:
            raise SalesforceError("EXTERNAL_DISABLED")
        self.credential_supplier = credential_supplier
        self.records = []

    def get(self, url, *, deadline, max_bytes, cancelled=lambda: False):
        start = time.monotonic()
        if (type(deadline) not in {int,float} or not math.isfinite(deadline) or not 0 < deadline-start <= 20
                or type(max_bytes) is not int or not 1 <= max_bytes <= self.config["max_response_bytes"]):
            raise SalesforceError("INVALID_DEADLINE")
        if not allowed_url(self.config, url):
            raise SalesforceError("SCOPE_DENIED")
        if cancelled():
            raise SalesforceError("CANCELLED")
        reserve = min(0.25,(deadline-start)/4)
        io_deadline = deadline-reserve
        token = None
        if self.mode == "external-salesforce":
            try:
                token = self.credential_supplier()
            except Exception:
                raise SalesforceError("CREDENTIAL_UNAVAILABLE") from None
            if not isinstance(token,str) or not 1 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise SalesforceError("CREDENTIAL_UNAVAILABLE")
        packet = encode({"config":self.config,"url":url,"local_base_url":self.local_base_url,
                         "token":token,"deadline":io_deadline,"max_bytes":max_bytes})
        if len(packet) > 16384 or time.monotonic() >= io_deadline or cancelled():
            raise SalesforceError("DEADLINE" if not cancelled() else "CANCELLED")
        process = None
        error = None
        try:
            process = subprocess.Popen([sys.executable,"-m","crm_agent.connectors.salesforce_worker"],
                cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                creationflags=0x08000000 if os.name == "nt" else 0)
            first = True
            while True:
                if cancelled():
                    raise SalesforceError("CANCELLED")
                remaining = io_deadline-time.monotonic()
                if remaining <= 0:
                    raise SalesforceError("DEADLINE")
                try:
                    stdout, _ = process.communicate(packet if first else None,timeout=min(0.02,remaining))
                    break
                except subprocess.TimeoutExpired:
                    first = False
            if process.returncode != 0 or len(stdout) > 400000:
                raise SalesforceError("TRANSPORT_ERROR")
            try:
                result = strict_json(stdout.decode("utf-8"))
                if not isinstance(result,dict):
                    raise ValueError()
                if "error" in result:
                    if result["error"] not in {"DEADLINE","BODY_LIMIT","TRANSPORT_ERROR","INVALID_REQUEST","INVALID_RESPONSE"}:
                        raise ValueError()
                    raise SalesforceError(result["error"])
                if set(result) != {"status","headers","body"}:
                    raise ValueError()
                raw = base64.b64decode(result["body"],validate=True)
                if len(raw) > max_bytes:
                    raise SalesforceError("BODY_LIMIT")
            except (ProtocolError,ValueError,TypeError,UnicodeError):
                raise SalesforceError("INVALID_RESPONSE") from None
            if time.monotonic() >= io_deadline:
                raise SalesforceError("DEADLINE")
            return HttpReply(result["status"],result["headers"],raw)
        except SalesforceError as e:
            error = e.code
            raise
        except Exception:
            error = "TRANSPORT_ERROR"
            raise SalesforceError(error) from None
        finally:
            if process is not None:
                cleanup_unknown = False
                try:
                    if process.poll() is None:
                        try:
                            process.kill()  # only the owned handle
                        except OSError:
                            if process.poll() is None:
                                cleanup_unknown = True
                        remaining = deadline-time.monotonic()
                        if remaining <= 0:
                            cleanup_unknown = True
                        else:
                            try:
                                process.communicate(timeout=remaining)
                            except subprocess.TimeoutExpired:
                                cleanup_unknown = True
                finally:
                    self.records.append({"pid":process.pid,"exit_code":process.poll(),"owned_child_exited":process.poll() is not None,
                        "duration_seconds":round(time.monotonic()-start,6),"peak_working_set_bytes":peak_working_set(process),
                        "error_code":"CLEANUP_UNKNOWN" if cleanup_unknown else error,"retries":0,"mode":self.mode})
                    for stream in (process.stdin,process.stdout,process.stderr):
                        if stream is not None:
                            stream.close()
                if cleanup_unknown:
                    raise SalesforceError("CLEANUP_UNKNOWN")
                if time.monotonic() >= deadline:
                    raise SalesforceError("DEADLINE")

