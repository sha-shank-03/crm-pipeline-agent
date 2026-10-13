"""Explicit authored HTTP double. No model, account or credential access."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from crm_agent.provider import Terminal
from crm_agent.scripted import ScriptedProvider


def config():
    return {"schema_version": 1, "model": "test/local-double", "provider": "local-double",
            "max_requests": 8, "max_output_tokens": 2048, "input_token_ceiling": 40960,
            "allocation_usd": "0", "reserve_per_request_usd": "0", "live_enabled": False,
            "dataset_version": "v1-authored-dev-20261008"}


def response(name="list_opportunities", arguments=None, *, identifier="call_1", generation="gen_double_1"):
    return json.dumps({"id": generation, "model": "test/local-double",
        "choices": [{"index": 0, "finish_reason": "tool_calls",
                     "message": {"role": "assistant", "content": None,
                                 "tool_calls": [{"id": identifier, "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(arguments or {})}}]}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}}).encode()


def observed_decision(packet):
    """Adapt actual wire observations to the existing explicitly authored fixture script."""
    messages = []
    pending = {}
    for item in packet["messages"]:
        if item["role"] == "user":
            messages.append({"role": "user", "content": item["content"]})
        elif item["role"] == "assistant":
            call = item["tool_calls"][0]
            pending[call["id"]] = call["function"]["name"]
        elif item["role"] == "tool":
            messages.append({"role": "tool", "tool": pending[item["tool_call_id"]],
                             "result": json.loads(item["content"])})
    return ScriptedProvider().next(messages, [], {})


class GatewayDouble:
    def __init__(self, responder=None):
        self.responder = responder
        self.requests = []
        self.release = threading.Event()
        self.entered = threading.Event()
        self.block_headers = False
        self.drip = False
        self.drop_response = False
        self.status = 200
        self.body_override = None
        self.handlers = []
        self.authorizations_seen = 0
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                owner.handlers.append(threading.current_thread())
                self.connection.settimeout(0.5)
                packet = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.authorizations_seen += int("Authorization" in self.headers)
                owner.requests.append({"path": self.path, "body": packet})
                owner.entered.set()
                if owner.block_headers:
                    owner.release.wait(4)
                if owner.drop_response:
                    self.connection.close()
                    return
                if owner.body_override is not None:
                    body = owner.body_override
                elif owner.responder:
                    body = owner.responder(packet, len(owner.requests))
                else:
                    decision = observed_decision(packet)
                    args = ({"disposition": decision.disposition, "reason": decision.reason,
                             "citations": list(decision.citations)} if isinstance(decision, Terminal) else decision.arguments)
                    body = response("finish" if isinstance(decision, Terminal) else decision.name, args,
                                    identifier="call_" + str(len(owner.requests)),
                                    generation="gen_double_" + str(len(owner.requests)))
                try:
                    self.send_response(owner.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if owner.drip:
                        for byte in body:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            if owner.release.wait(0.02):
                                break
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
                    pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        for handler in self.handlers:
            handler.join(timeout=1)
        if self.thread.is_alive() or any(handler.is_alive() for handler in self.handlers):
            raise AssertionError("Owned double thread did not exit")
