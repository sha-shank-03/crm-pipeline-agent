"""Actual loopback HTTP transport double; not a model, execute only under the M2 lease."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest

from crm_agent.providers.ollama_http import OllamaHttpProvider, ProviderError
from crm_agent.protocol import ProtocolError
from crm_agent.provider import Terminal, ToolCall
from crm_agent.tools import tool_schemas
from tests.test_m2_components import response

class HttpTransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.responses = []
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                outer.requests.append({"path": self.path, "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
                status, body, headers, delay = outer.responses.pop(0) if outer.responses else (200, response("finish", {"disposition": "HANDOFF", "reason": "Test double complete", "citations": []}), {}, 0)
                if delay: time.sleep(delay)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for key, value in headers.items(): self.send_header(key, value)
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.provider = OllamaHttpProvider(self.base, "test-local", allow_live_model=True)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def call(self, messages=None, seconds=1):
        return self.provider.next(messages or [{"role": "user", "content": "Review fictional pipeline"}], tool_schemas(), {"remaining_seconds": seconds})

    def test_actual_multiturn_request_replays_tool_result(self):
        self.responses += [(200, response(), {}, 0), (200, response("get_opportunity", {"id": "opp-101", "include_history": True}, prompt_eval_count=7, eval_count=2), {}, 0)]
        first = self.call()
        self.assertEqual(first, ToolCall("list_opportunities", {}))
        second = self.call([{"role": "user", "content": "Review"}, {"role": "assistant", "tool": first.name, "arguments": first.arguments},
                            {"role": "tool", "tool": first.name, "result": {"opportunities": [{"id": "opp-101"}]}}])
        self.assertEqual(second.name, "get_opportunity")
        request = self.requests[-1]
        self.assertEqual(request["path"], "/api/chat")
        self.assertFalse(request["body"]["stream"])
        self.assertEqual(request["body"]["messages"][-1]["tool_name"], "list_opportunities")
        self.assertEqual(self.provider.usage, [{"prompt_eval_count": None, "eval_count": None}, {"prompt_eval_count": 7, "eval_count": 2}])

    def test_actual_unknown_tool_and_malformed_response_fail_closed(self):
        for body in [response("approve", {}), b'{"invalid":', response(done=False)]:
            self.responses.append((200, body, {}, 0))
            with self.assertRaises(ProtocolError): self.call()

    def test_multiple_mutation_calls_rejected(self):
        value = json.loads(response("stage_proposal", {}))
        value["message"]["tool_calls"] *= 2
        self.responses.append((200, json.dumps(value).encode(), {}, 0))
        with self.assertRaises(ProviderError): self.call()

    def test_redirect_does_not_follow_even_to_same_server(self):
        self.responses.append((302, b"", {"Location": self.base + "/other"}, 0))
        with self.assertRaises(ProviderError): self.call()
        self.assertEqual(len(self.requests), 1)

    def test_one_transient_retry_and_no_unbounded_retry(self):
        self.responses += [(503, b"unavailable", {}, 0), (200, response(), {}, 0)]
        self.assertEqual(self.call().name, "list_opportunities")
        self.responses += [(503, b"unavailable", {}, 0), (503, b"unavailable", {}, 0)]
        with self.assertRaises(ProviderError): self.call()
        self.assertEqual(len(self.requests), 4)

    def test_body_cap_rejects_oversized_response(self):
        self.responses.append((200, b"x" * 65537, {}, 0))
        with self.assertRaises(ProviderError): self.call()

    def test_total_deadline_handles_slow_provider(self):
        self.responses.append((200, response(), {}, 0.2))
        start = time.monotonic()
        with self.assertRaises(ProviderError): self.call(seconds=0.05)
        self.assertLess(time.monotonic() - start, 0.5)

    def test_terminal_disposition_round_trip(self):
        self.assertEqual(self.call(), Terminal("HANDOFF", "Test double complete"))
