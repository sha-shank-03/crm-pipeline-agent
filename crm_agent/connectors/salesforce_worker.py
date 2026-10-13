"""One closed GET exchange. Token arrives through private stdin, never argv/environment."""
import base64
import math
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from crm_agent.protocol import encode, strict_json
from .salesforce import SalesforceError, allowed_url, profile
from .salesforce_http import local_base


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def exchange(packet):
    if not isinstance(packet,dict) or set(packet) != {"config","url","local_base_url","token","deadline","max_bytes"}:
        raise SalesforceError("INVALID_REQUEST")
    config = profile(packet["config"])
    url, token, base = packet["url"], packet["token"], packet["local_base_url"]
    deadline, cap = packet["deadline"], packet["max_bytes"]
    if (not allowed_url(config,url) or type(deadline) not in {int,float} or not math.isfinite(deadline)
            or type(cap) is not int or not 1 <= cap <= config["max_response_bytes"]):
        raise SalesforceError("INVALID_REQUEST")
    if base is not None:
        if not local_base(base) or token is not None:
            raise SalesforceError("INVALID_REQUEST")
        parts = urlsplit(url)
        target = base+parts.path+("?" + parts.query if parts.query else "")
    else:
        if (not config["external_enabled"] or not isinstance(token,str) or not 1 <= len(token) <= 4096
                or any(ord(c)<33 or ord(c)>126 for c in token)):
            raise SalesforceError("INVALID_REQUEST")
        target = url
    def remaining():
        n = deadline-time.monotonic()
        if n <= 0:
            raise SalesforceError("DEADLINE")
        return n
    headers = {"Accept":"application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = Request(target,headers=headers,method="GET")
    opener = build_opener(ProxyHandler({}),NoRedirect())
    response = None
    try:
        try:
            response = opener.open(request,timeout=remaining())
        except HTTPError as e:
            response = e
        remaining()
        selected = {}
        for name in ("Content-Type","Sforce-Limit-Info"):
            values = response.headers.get_all(name,[])
            if len(values)>1 or any(len(v)>16384 for v in values):
                raise SalesforceError("INVALID_RESPONSE")
            if values:
                selected[name] = values[0]
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdecimal() or int(length)>cap):
            raise SalesforceError("BODY_LIMIT")
        raw = bytearray()
        while True:
            seconds = remaining()
            try:
                response.fp.raw._sock.settimeout(seconds)
            except AttributeError:
                pass
            part = response.read(min(16384,cap+1-len(raw)))
            remaining()  # includes EOF
            raw.extend(part)
            if len(raw)>cap:
                raise SalesforceError("BODY_LIMIT")
            if not part:
                break
        remaining()
        return {"status":response.status,"headers":selected,"body":base64.b64encode(raw).decode("ascii")}
    finally:
        if response is not None:
            response.close()


def main():
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw)>16384:
            raise SalesforceError("INVALID_REQUEST")
        result = exchange(strict_json(raw.decode("utf-8")))
    except SalesforceError as e:
        result = {"error":e.code if e.code in {"DEADLINE","BODY_LIMIT","INVALID_RESPONSE"} else "INVALID_REQUEST"}
    except (TimeoutError,socket.timeout):
        result = {"error":"DEADLINE"}
    except URLError as e:
        result = {"error":"DEADLINE" if isinstance(e.reason,(TimeoutError,socket.timeout)) else "TRANSPORT_ERROR"}
    except Exception:
        result = {"error":"TRANSPORT_ERROR"}
    sys.stdout.buffer.write(encode(result)+b"\n")


if __name__ == "__main__":
    main()

