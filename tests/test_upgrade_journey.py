"""Leased U1 Java state checks; all model responses come from the authored loopback double."""
import copy
from pathlib import Path
import tempfile
import unittest

from crm_agent.java_client import JavaClient
from crm_agent.loop import run_agent, resume, request_cancel
from crm_agent.providers.ai_gateway import AiGatewayProvider
from crm_agent.providers.owned_http import OwnedHttpTransport
from crm_agent.run_state import load
from crm_agent.protocol import strict_json
from tools.gateway_double import GatewayDouble, config, response
from tools.upgrade_check import committed

ROOT = Path(__file__).resolve().parents[1]


class GatewayJourneyTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "work")
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)

    def initialize(self):
        self.store = self.output / "crm"
        self.operator = JavaClient(self.store, "fixture-operator")
        fixture = strict_json((ROOT / "fixtures/dev/approved-update.json").read_text(encoding="utf-8"))
        self.operator.call("init", {"fixture": fixture})
        self.before = committed(self.store)

    def test_gateway_unknown_tool_halts_before_any_java_tool(self):
        self.initialize()
        with GatewayDouble(lambda packet, count: response("approve")) as server:
            transport = OwnedHttpTransport(url=server.url, local_double=True)
            provider = AiGatewayProvider(config(), transport, local_double=True)
            run = run_agent(JavaClient(self.store, "proposer-north"), provider, self.output / "run")
            self.assertEqual(run["state"], "HANDOFF")
            self.assertEqual(run["tool_calls"], 0)
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(committed(self.store), self.before)
            self.assertEqual(run["provider_accounting"]["requests"], 1)

    def test_actual_java_out_of_scope_denial_preserves_state(self):
        self.initialize()
        with GatewayDouble(lambda packet, count: response("get_opportunity",
                    {"id": "opp-101", "include_history": True})) as server:
            transport = OwnedHttpTransport(url=server.url, local_double=True)
            provider = AiGatewayProvider(config(), transport, local_double=True)
            run = run_agent(JavaClient(self.store, "proposer-south"), provider, self.output / "run")
            self.assertEqual(run["state"], "HANDOFF")
            self.assertEqual(run["reason"], "PERMISSION_DENIED")
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(committed(self.store), self.before)

    def test_gateway_stale_approval_and_cancel_prevent_apply(self):
        for cancel in [False, True]:
            with self.subTest(cancel=cancel):
                destination = self.output / ("cancel" if cancel else "stale")
                destination.mkdir()
                store = destination / "crm"
                operator = JavaClient(store, "fixture-operator")
                fixture = strict_json((ROOT / "fixtures/dev/approved-update.json").read_text(encoding="utf-8"))
                operator.call("init", {"fixture": fixture})
                proposer = JavaClient(store, "proposer-north")
                manager = JavaClient(store, "manager-north")
                executor = JavaClient(store, "executor-north")
                with GatewayDouble() as server:
                    provider = AiGatewayProvider(config(), OwnedHttpTransport(url=server.url, local_double=True),
                                                 local_double=True)
                    run = run_agent(proposer, provider, destination / "run")
                record = destination / "run/run.json"
                manager.call("approve", {"proposal_id": run["proposal_id"], "digest": run["proposal_digest"]})
                if cancel:
                    request_cancel(record)
                    expected = "CANCELLED"
                else:
                    operator.call("edit_opportunity", {"opportunity_id": "opp-101", "expected_version": 7,
                                                       "next_step": "Rep concurrent edit"})
                    expected = "STALE"
                before = committed(store)
                result = resume(load(record), record, proposer, executor)
                self.assertEqual(result["state"], expected)
                self.assertEqual(committed(store), before)
                self.assertFalse(before["receipts"])
