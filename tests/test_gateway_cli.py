"""Pure CLI gates; synthetic profile, credential and transport calls are forbidden."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import unittest
from unittest.mock import patch

import crm

ROOT = Path(__file__).resolve().parents[1]


class GatewayCliTests(unittest.TestCase):
    def test_zero_allocation_profile_cannot_load_key_or_send_request(self):
        capture = io.StringIO()
        with patch("os.getenv", side_effect=AssertionError("Credential read forbidden")) as getter, \
             patch("crm_agent.providers.owned_http.OwnedHttpTransport.post",
                   side_effect=AssertionError("Network forbidden")) as sender, redirect_stderr(capture):
            code = crm.main(["run", "--provider", "ai-gateway", "--allow-live-model",
                "--gateway-config", str(ROOT / "configs/gateway.disabled.json"),
                "--store", str(ROOT / "work/never-initialized"), "--out", str(ROOT / "work/never-dispatched")])
        self.assertEqual(code, 2)
        self.assertIn("LIVE_DISABLED", capture.getvalue())
        getter.assert_not_called()
        sender.assert_not_called()
        self.assertFalse((ROOT / "work/never-dispatched").exists())

    def test_gateway_disallows_user_endpoint_override(self):
        capture = io.StringIO()
        with patch("os.getenv", side_effect=AssertionError("Credential read forbidden")) as getter, \
             patch("crm_agent.providers.owned_http.OwnedHttpTransport.post") as sender, redirect_stderr(capture):
            code = crm.main(["run", "--provider", "ai-gateway", "--allow-live-model",
                "--gateway-config", str(ROOT / "configs/gateway.disabled.json"),
                "--base-url", "https://example.com", "--store", str(ROOT / "work/never-initialized"),
                "--out", str(ROOT / "work/never-dispatched")])
        self.assertEqual(code, 2)
        self.assertIn("INVALID_CONFIG", capture.getvalue())
        getter.assert_not_called()
        sender.assert_not_called()
