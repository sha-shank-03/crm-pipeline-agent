"""Pure schema/accounting controls. No sockets, processes, Java, keys or models."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from crm_agent.providers.ai_gateway import AiGatewayProvider, parse_response, wire_messages
from crm_agent.providers.owned_http import GatewayError
from crm_agent.provider import Terminal
from crm_agent.tools import tool_schemas
from tools.gateway_double import config, response


class GatewayProtocolTests(unittest.TestCase):
    def test_one_tool_and_usage(self):
        decision, call_id, generation, usage = parse_response(response(), "test/local-double")
        self.assertEqual(decision.name, "list_opportunities")
        self.assertEqual(call_id, "call_1")
        self.assertEqual(generation, "gen_double_1")
        self.assertEqual(usage, {"input_tokens": 12, "output_tokens": 3})

    def test_unknown_multiple_malformed_and_false_completion(self):
        values = [response("approve"), b'{"id":1,"id":2}']
        for mutation in ("multiple", "malformed", "applied", "truncated", "wrong_model", "bad_usage"):
            value = json.loads(response())
            message = value["choices"][0]["message"]
            if mutation == "multiple": message["tool_calls"] *= 2
            if mutation == "malformed": message["tool_calls"][0]["function"]["arguments"] = '{"bad":'
            if mutation == "applied":
                message["tool_calls"][0]["function"] = {"name": "finish", "arguments": json.dumps(
                    {"disposition": "APPLIED", "reason": "Claimed success", "citations": []})}
            if mutation == "truncated": value["choices"][0]["finish_reason"] = "length"
            if mutation == "wrong_model": value["model"] = "other/model"
            if mutation == "bad_usage": value["usage"]["completion_tokens"] = 2049
            values.append(json.dumps(value).encode())
        for payload in values:
            with self.subTest(payload=payload[:20]):
                with self.assertRaises(GatewayError):
                    parse_response(payload, "test/local-double")

    def test_call_id_result_pairing_and_no_authority_tool(self):
        messages = [{"role": "user", "content": "Review"},
                    {"role": "assistant", "tool": "list_opportunities", "arguments": {}, "call_id": "call_1"},
                    {"role": "tool", "tool": "list_opportunities", "result": {"opportunities": []}, "call_id": "call_1"}]
        wire = wire_messages(messages)
        self.assertEqual(wire[-1]["tool_call_id"], wire[-2]["tool_calls"][0]["id"])
        messages[-1]["call_id"] = "call_other"
        with self.assertRaises(GatewayError): wire_messages(messages)
        self.assertFalse({"approve", "apply", "export"} & {tool["name"] for tool in tool_schemas()})

    def test_zero_allocation_rejected_without_accessing_supplier(self):
        transport = Mock(local_double=False)
        disabled = config()
        disabled["live_enabled"] = True
        with self.assertRaises(GatewayError): AiGatewayProvider(disabled, transport)
        transport.post.assert_not_called()

    def test_no_retry_and_unknown_cost_reservation_preserved(self):
        transport = Mock(local_double=False)
        transport.post.side_effect = GatewayError("TRANSPORT_UNKNOWN")
        live = dict(config(), live_enabled=True, allocation_usd="0.01", reserve_per_request_usd="0.01")
        provider = AiGatewayProvider(live, transport)
        with self.assertRaises(GatewayError):
            provider.next([{"role": "user", "content": "Review"}], tool_schemas(), {"remaining_seconds": 1})
        self.assertEqual(transport.post.call_count, 1)
        self.assertEqual(provider.standalone["requests"], 1)
        self.assertEqual(provider.standalone["reserved_cost_usd"], "0.01")
        self.assertIsNone(provider.standalone["actual_cost_usd"])
        with self.assertRaises(GatewayError):
            provider.next([{"role": "user", "content": "Review"}], tool_schemas(), {"remaining_seconds": 1})
        self.assertEqual(transport.post.call_count, 1)

    def test_request_cap_and_missing_usage_are_not_zero_usage(self):
        transport = Mock(local_double=True)
        value = json.loads(response())
        del value["usage"]
        transport.post.return_value = json.dumps(value).encode()
        provider = AiGatewayProvider(dict(config(), max_requests=1), transport, local_double=True)
        provider.next([{"role": "user", "content": "Review"}], tool_schemas(), {"remaining_seconds": 1})
        self.assertEqual(provider.usage[0], {"input_tokens": None, "output_tokens": None})
        with self.assertRaises(GatewayError):
            provider.next([{"role": "user", "content": "Review"}], tool_schemas(), {"remaining_seconds": 1})
        self.assertEqual(transport.post.call_count, 1)

    def test_invalid_config_deadline_and_oversized_request_dispatch_nothing(self):
        transport = Mock(local_double=True)
        for settings in [dict(config(), max_requests=9), dict(config(), max_output_tokens=True),
                         dict(config(), unexpected="secret"), dict(config(), allocation_usd="NaN")]:
            with self.assertRaises(GatewayError): AiGatewayProvider(settings, transport, local_double=True)
        provider = AiGatewayProvider(config(), transport, local_double=True)
        for seconds in [float("nan"), float("inf"), 0, -1]:
            with self.assertRaises(GatewayError):
                provider.next([{"role": "user", "content": "Review"}], tool_schemas(), {"remaining_seconds": seconds})
        with self.assertRaises(GatewayError):
            provider.next([{"role": "user", "content": "x" * 32768}], tool_schemas(), {"remaining_seconds": 1})
        transport.post.assert_not_called()

    def test_finish_cannot_claim_applied_state(self):
        decision, *_ = parse_response(response("finish", {"disposition": "HANDOFF",
            "reason": "Conflicting evidence", "citations": []}), "test/local-double")
        self.assertEqual(decision, Terminal("HANDOFF", "Conflicting evidence"))
