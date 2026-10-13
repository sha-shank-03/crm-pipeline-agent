"""Leased native socket/owned-process tests; all endpoints are literal loopback doubles."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from crm_agent.providers.owned_http import GatewayError, OwnedHttpTransport
from tools.gateway_double import GatewayDouble, response

ROOT = Path(__file__).resolve().parents[1]


class OwnedTransportTests(unittest.TestCase):
    def test_success_and_owned_exit(self):
        with GatewayDouble(lambda packet, count: response()) as server:
            transport = OwnedHttpTransport(url=server.url, local_double=True)
            result = transport.post(b'{"messages":[]}', 3)
            self.assertEqual(json.loads(result)["id"], "gen_double_1")
            self.assertTrue(transport.records[0]["owned_child_exited"])
            self.assertEqual(transport.records[0]["exit_code"], 0)
            self.assertEqual(server.authorizations_seen, 0)

    def test_http_codes_each_dispatch_exactly_once(self):
        with GatewayDouble(lambda packet, count: response()) as server:
            for status in [401, 402, 403, 429, 500, 503]:
                server.status = status
                transport = OwnedHttpTransport(url=server.url, local_double=True)
                before = len(server.requests)
                with self.assertRaises(GatewayError) as error:
                    transport.post(b'{"messages":[]}', 3)
                self.assertEqual(error.exception.code, "HTTP_" + str(status))
                self.assertEqual(len(server.requests), before + 1)
                self.assertTrue(transport.records[-1]["owned_child_exited"])

    def test_header_and_drip_deadlines_include_child_cleanup(self):
        for drip in [False, True]:
            with self.subTest(drip=drip):
                with GatewayDouble(lambda packet, count: response()) as server:
                    server.block_headers = not drip
                    server.drip = drip
                    transport = OwnedHttpTransport(url=server.url, local_double=True)
                    start = time.monotonic()
                    with self.assertRaises(GatewayError) as error:
                        transport.post(b'{"messages":[]}', 0.8)
                    self.assertIn(error.exception.code, {"DEADLINE", "TRANSPORT_UNKNOWN"})
                    self.assertLess(time.monotonic() - start, 1.2)
                    self.assertLessEqual(len(server.requests), 1)
                    self.assertTrue(transport.records[-1]["owned_child_exited"])

    def test_inflight_cancellation_stops_owned_child(self):
        (ROOT / "work").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            marker = Path(directory) / "cancel.requested"
            with GatewayDouble(lambda packet, count: response()) as server:
                server.block_headers = True
                transport = OwnedHttpTransport(url=server.url, local_double=True, cancel_file=marker)
                def cancel():
                    if server.entered.wait(2):
                        marker.write_text("cancel\n")
                thread = threading.Thread(target=cancel, daemon=True)
                thread.start()
                with self.assertRaises(GatewayError) as error:
                    transport.post(b'{"messages":[]}', 3)
                thread.join(timeout=1)
                self.assertFalse(thread.is_alive())
                self.assertEqual(error.exception.code, "CANCELLED")
                self.assertEqual(len(server.requests), 1)
                self.assertTrue(transport.records[-1]["owned_child_exited"])

    def test_dropped_response_and_body_cap_no_retry(self):
        with GatewayDouble(lambda packet, count: response()) as server:
            server.drop_response = True
            transport = OwnedHttpTransport(url=server.url, local_double=True)
            with self.assertRaises(GatewayError) as error:
                transport.post(b'{"messages":[]}', 3)
            self.assertEqual(error.exception.code, "TRANSPORT_UNKNOWN")
            self.assertEqual(len(server.requests), 1)
            self.assertTrue(transport.records[-1]["owned_child_exited"])
        with GatewayDouble(lambda packet, count: response()) as server:
            server.body_override = b"x" * 65537
            transport = OwnedHttpTransport(url=server.url, local_double=True)
            with self.assertRaises(GatewayError) as error:
                transport.post(b'{"messages":[]}', 3)
            self.assertEqual(error.exception.code, "RESPONSE_SIZE")
            self.assertEqual(len(server.requests), 1)

    def test_literal_endpoint_and_double_credential_guard(self):
        for url in ["http://localhost:123/v1/chat/completions", "http://127.0.0.1:123/other",
                    "https://example.com/v1/chat/completions"]:
            with self.assertRaises(ValueError): OwnedHttpTransport(url=url, local_double=True)
        supplier = lambda: "SYNTHETIC_SENTINEL"
        with self.assertRaises(GatewayError):
            OwnedHttpTransport(url="http://127.0.0.1:123/v1/chat/completions",
                               local_double=True, credential_supplier=supplier)
        transport = OwnedHttpTransport(url="http://127.0.0.1:123/v1/chat/completions", local_double=True)
        with patch("crm_agent.providers.owned_http.subprocess.Popen") as popen:
            with self.assertRaises(GatewayError): transport.post(b"x" * 32769, 1)
            for seconds in [0, -1, float("nan"), float("inf")]:
                with self.assertRaises(GatewayError): transport.post(b"{}", seconds)
            popen.assert_not_called()
