"""Helpers for leased M2 integration; importing this module launches nothing."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from crm_agent.java_client import JavaClient, DomainError
from crm_agent.loop import run_agent
from crm_agent.protocol import encode, strict_json
from crm_agent.scripted import ScriptedProvider
from tests.test_m1_integration import committed_state

ROOT = Path(__file__).resolve().parents[1]

def java_command(store, principal="executor-north", failpoint=None, main="crm.Main"):
    from tools.build import verify_dependency
    classpath = os.pathsep.join([str(ROOT / "build/classes")] + list(map(str, verify_dependency())))
    command = ["java", "-Xmx64m", "-cp", classpath, main]
    if main == "crm.Main":
        command += ["--store", str(store), "--principal", principal, "--as-of", "2026-10-08"]
        if failpoint:
            command += ["--test-failpoint", failpoint]
    return command

class M2Fixture(unittest.TestCase):
    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "work")
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)
        self.store = self.output / "crm"
        self.operator = JavaClient(self.store, "fixture-operator")
        self.proposer = JavaClient(self.store, "proposer-north")
        self.manager = JavaClient(self.store, "manager-north")
        self.executor = JavaClient(self.store, "executor-north")
        self.fixture = strict_json((ROOT / "fixtures/dev/approved-update.json").read_text())
        self.operator.call("init", {"fixture": self.fixture})
        self.run = run_agent(self.proposer, ScriptedProvider(), self.output / "run")
        self.record = self.output / "run/run.json"
        action = committed_state(self.store)["proposals"][self.run["proposal_id"]]["action"]
        self.stage_args = {key: copy.deepcopy(action[key]) for key in ["run_id", "opportunity_id", "expected_version", "expected_evidence_revision", "expected_account_revision", "changes", "citations"]}

    def approve(self):
        self.approval = self.manager.call("approve", {"proposal_id": self.run["proposal_id"], "digest": self.run["proposal_digest"]})["approval"]
        self.apply_args = {"proposal_id": self.run["proposal_id"], "approval_id": self.approval["id"], "digest": self.run["proposal_digest"]}
        return self.approval

    def change(self, arguments):
        return self.operator.call("fixture_change", arguments)

    def unchanged_error(self, client, operation, args, code, key=None):
        before = committed_state(self.store)
        generations = sorted(self.store.glob("generation-*.json"))
        with self.assertRaises(DomainError) as failure:
            client.call(operation, args, key)
        self.assertEqual(failure.exception.code, code)
        self.assertEqual(committed_state(self.store), before)
        self.assertEqual(sorted(self.store.glob("generation-*.json")), generations)

    def raw(self, payload, principal="proposer-north", failpoint=None):
        return subprocess.run(java_command(self.store, principal, failpoint), input=payload, capture_output=True, timeout=8, check=False)

    def envelope(self, operation, arguments, key=None):
        return encode({"schema_version": 1, "request_id": key or str(uuid.uuid4()), "operation": operation, "args": arguments})
