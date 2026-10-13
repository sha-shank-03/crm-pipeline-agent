"""One bounded HTTP exchange; stdin/stdout IPC only, no credentials from environment."""
import base64
import http.client
import ipaddress
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ..protocol import encode, strict_json

GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"


def validate_url(url, local_double):
    if not local_double:
        if url != GATEWAY_URL:
            raise ValueError("Endpoint is not the fixed Gateway endpoint")
        return
    parts = urlsplit(url)
    address = ipaddress.ip_address(parts.hostname or "")
    if (parts.scheme != "http" or not address.is_loopback or not parts.port or
            parts.username or parts.password or parts.query or parts.fragment or
            parts.path != "/v1/chat/completions"):
        raise ValueError("Local double requires a literal loopback endpoint")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError("Redirect rejected")


def exchange(packet):
    validate_url(packet["url"], packet["local_double"])
    deadline = packet["deadline"]
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError()
        return value
    body = base64.b64decode(packet["body"], validate=True)
    if len(body) > 32768:
        return {"error": "REQUEST_SIZE"}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if not packet["local_double"]:
        token = packet["token"]
        if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
            return {"error": "AUTH"}
        headers["Authorization"] = "Bearer " + token
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(packet["url"], body, headers, method="POST")
    try:
        with opener.open(request, timeout=remaining()) as response:
            remaining()
            if response.status != 200:
                return {"error": "HTTP_STATUS", "status": response.status}
            if response.headers.get_content_type() != "application/json":
                return {"error": "CONTENT_TYPE"}
            payload = bytearray()
            while len(payload) <= 65536:
                seconds = remaining()
                if response.fp is not None and getattr(response.fp, "raw", None) is not None:
                    response.fp.raw._sock.settimeout(seconds)
                chunk = response.read1(min(8192, 65537 - len(payload)))
                remaining()
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) > 65536:
                return {"error": "RESPONSE_SIZE"}
            remaining()
            return {"payload": base64.b64encode(payload).decode("ascii")}
    except HTTPError as error:
        error.close()
        return {"error": "HTTP_STATUS", "status": error.code}
    except (URLError, TimeoutError, socket.timeout, http.client.HTTPException, OSError):
        return {"error": "TRANSPORT_UNKNOWN"}
    except ValueError:
        return {"error": "REDIRECT_OR_PROTOCOL"}


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            result = {"error": "IPC_SIZE"}
        else:
            result = exchange(strict_json(raw.decode("utf-8")))
    except Exception:
        # Never serialize exception text, request, headers, token or response body.
        result = {"error": "WORKER_PROTOCOL"}
    sys.stdout.buffer.write(encode(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
