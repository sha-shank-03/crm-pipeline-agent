import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from crm_agent.java_client import DomainError, OutcomeUnknown
from crm_agent.loop import run_agent
from crm_agent.protocol import ProtocolError, strict_json
from crm_agent.provider import Terminal, ToolCall
from crm_agent.run_state import load
from crm_agent.scripted import ScriptedProvider
from crm_agent.tools import validate_call

ROOT = Path(__file__).resolve().parents[1]


class Observations:
    """Protocol double only: no CRM mutation policy is simulated or scored here."""
    as_of = "2026-10-08"

    def __init__(self, output):
        self.store = Path(output) / "fake-store"
        self.record = Path(output) / "run/run.json"
        self.calls = []
        fixture = strict_json((ROOT / "fixtures/dev/approved-update.json").read_text(encoding="utf-8"))
        self.opportunity = fixture["opportunities"]["opp-101"]
        self.note = fixture["notes"]["note-101"]
        for line in self.note["lines"]:
            line["line_hash"] = hashlib.sha256(line["text"].encode()).hexdigest()

    def call(self, operation, arguments, request_id=None):
        self.calls.append((operation, arguments, request_id))
        if operation == "list_opportunities": return {"opportunities": [self.opportunity]}
        if operation == "get_opportunity": return dict(self.opportunity, account_revision=1)
        if operation == "list_notes": return {"notes": [{"id": "note-101"}]}
        if operation == "read_note": return self.note
        if operation == "stage_proposal":
            pending = load(self.record)["pending_stage"]
            if pending["request_id"] != request_id or pending["arguments"] != arguments:
                raise AssertionError("Mutation intent was not durably saved before dispatch")
            return {"proposal": {"id": "p-test", "digest": "d" * 64}}
        raise AssertionError("Unexpected authority exposed")


class PythonBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "work").mkdir(exist_ok=True)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ProtocolError): strict_json('{"x": null, "x": 1}')

    def test_nonfinite_number_rejected(self):
        with self.assertRaises(ProtocolError): strict_json('{"x": NaN}')

    def test_unregistered_mutation_rejected(self):
        for tool in ("apply", "approve", "edit_opportunity", "shell"):
            with self.subTest(tool=tool), self.assertRaises(ProtocolError): validate_call(ToolCall(tool, {}))

    def test_provider_cannot_override_identity(self):
        with self.assertRaises(ProtocolError): validate_call(ToolCall("list_opportunities", {"principal": "manager-north"}))

    def test_bool_is_not_integer_version(self):
        with self.assertRaises(ProtocolError):
            validate_call(ToolCall("stage_proposal", {"opportunity_id": "x", "expected_version": True,
                "expected_evidence_revision": 1, "expected_account_revision": 1, "changes": [], "citations": []}))

    def test_script_consumes_observations_and_stops_before_approval(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            observation = Observations(directory)
            run = run_agent(observation, ScriptedProvider(), Path(directory) / "run")
            self.assertEqual(run["state"], "WAITING_APPROVAL")
            self.assertEqual([call[0] for call in observation.calls],
                             ["list_opportunities", "get_opportunity", "list_notes", "read_note", "stage_proposal"])
            self.assertEqual(run["provider_mode"], "scripted")
            self.assertIsNone(run["model_id"])
            self.assertFalse(run["model_quality_evaluated"])
            self.assertIsNone(run["pending_stage"])
            self.assertEqual(load(observation.record)["proposal_id"], "p-test")

    def test_endless_read_provider_is_bounded(self):
        class Endless:
            mode, model_id = "test-double", None
            def next(self, messages, tools, limits): return ToolCall("list_opportunities", {})
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            observation = Observations(directory)
            run = run_agent(observation, Endless(), Path(directory) / "run")
            self.assertEqual(run["state"], "HANDOFF")
            self.assertEqual(run["tool_calls"], 16)

    def test_malformed_decision_cannot_dispatch(self):
        class Bad:
            mode, model_id = "test-double", None
            def next(self, messages, tools, limits): return ToolCall("approve", {})
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            observation = Observations(directory)
            run = run_agent(observation, Bad(), Path(directory) / "run")
            self.assertEqual(run["state"], "HANDOFF")
            self.assertEqual(observation.calls, [])

    def test_uncertain_stage_preserves_exact_key(self):
        class Interrupted(Observations):
            def call(self, operation, arguments, request_id=None):
                if operation == "stage_proposal":
                    super().call(operation, arguments, request_id)
                    raise OutcomeUnknown(request_id)
                return super().call(operation, arguments, request_id)
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            observation = Interrupted(directory)
            run = run_agent(observation, ScriptedProvider(), Path(directory) / "run")
            self.assertEqual(run["state"], "OUTCOME_UNKNOWN")
            self.assertEqual(run["pending_stage"]["request_id"], observation.calls[-1][2])

    def test_terminal_cannot_claim_applied(self):
        class FalseSuccess:
            mode, model_id = "test-double", None
            def next(self, messages, tools, limits): return Terminal("APPLIED", "Trust me")
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            observation = Observations(directory)
            run = run_agent(observation, FalseSuccess(), Path(directory) / "run")
            self.assertEqual(run["state"], "HANDOFF")
            self.assertEqual(observation.calls, [])

    def test_existing_run_not_overwritten(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            observation = Observations(directory)
            output = Path(directory) / "run"
            run_agent(observation, ScriptedProvider(), output)
            before = observation.record.read_bytes()
            with self.assertRaises(ValueError): run_agent(observation, ScriptedProvider(), output)
            self.assertEqual(before, observation.record.read_bytes())


if __name__ == "__main__":
    (ROOT / "work").mkdir(exist_ok=True)
    unittest.main()
