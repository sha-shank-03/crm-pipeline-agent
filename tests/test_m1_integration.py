"""Real Java subprocess and independent persisted-state assertions; requires integration lease locally."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
import uuid

from crm_agent.java_client import DomainError, JavaClient
from crm_agent.loop import run_agent, resume
from crm_agent.protocol import strict_json
from crm_agent.run_state import load
from crm_agent.scripted import ScriptedProvider

ROOT = Path(__file__).resolve().parents[1]


def committed_state(store):
    paths = sorted(Path(store).glob("generation-*.json"))
    return strict_json(paths[-1].read_text(encoding="utf-8"))


class M1IntegrationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "work")
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)
        self.store = self.output / "crm"
        self.proposer = JavaClient(self.store, "proposer-north")
        self.manager = JavaClient(self.store, "manager-north")
        self.executor = JavaClient(self.store, "executor-north")
        self.operator = JavaClient(self.store, "fixture-operator")
        fixture = strict_json((ROOT / "fixtures/dev/approved-update.json").read_text(encoding="utf-8"))
        self.operator.call("init", {"fixture": fixture})
        self.before = committed_state(self.store)
        self.run = run_agent(self.proposer, ScriptedProvider(), self.output / "run")
        self.record = self.output / "run/run.json"

    def approve(self):
        return self.manager.call("approve", {"proposal_id": self.run["proposal_id"], "digest": self.run["proposal_digest"]})["approval"]

    def test_normal_run_stops_for_explicit_review(self):
        self.assertEqual(self.run["state"], "WAITING_APPROVAL")
        state = committed_state(self.store)
        self.assertEqual(state["opportunities"], self.before["opportunities"])
        self.assertEqual(state["approvals"], {})
        self.assertEqual(state["receipts"], {})
        self.assertEqual([event["type"] for event in state["audit"]], ["PROPOSAL_STAGED"])
        waiting = resume(load(self.record), self.record, self.proposer, self.executor)
        self.assertEqual(waiting["state"], "WAITING_APPROVAL")
        self.assertEqual(committed_state(self.store), state)

    def test_approved_patch_and_duplicate_receipt(self):
        self.approve()
        run = resume(load(self.record), self.record, self.proposer, self.executor)
        self.assertEqual(run["state"], "APPLIED")
        state = committed_state(self.store)
        before, after = self.before["opportunities"]["opp-101"], state["opportunities"]["opp-101"]
        for key in before:
            if key not in {"close_date", "next_step", "version", "history"}:
                self.assertEqual(before[key], after[key], key)
        self.assertEqual(after["version"], 8)
        self.assertEqual(after["close_date"], "2026-11-12")
        self.assertEqual(after["next_step"], "Send revised agreement by 2026-10-09.")
        self.assertEqual(len(after["history"]), len(before["history"]) + 1)
        self.assertEqual(sum(event["type"] == "APPLIED" for event in state["audit"]), 1)
        self.assertEqual(len(state["receipts"]), 1)
        again = resume(load(self.record), self.record, self.proposer, self.executor)
        self.assertEqual(again["receipt"], run["receipt"])
        self.assertEqual(committed_state(self.store), state)
        pending = run["pending_apply"]
        changed = dict(pending["arguments"], digest="0" * 64)
        with self.assertRaises(DomainError) as conflict:
            self.executor.call("apply", changed, pending["request_id"])
        self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")
        with self.assertRaises(DomainError) as duplicate:
            self.executor.call("apply", pending["arguments"], str(uuid.uuid4()))
        self.assertEqual(duplicate.exception.code, "ALREADY_APPLIED")
        self.assertEqual(committed_state(self.store), state)

    def test_stale_approval_preserves_concurrent_edit(self):
        self.approve()
        self.operator.call("edit_opportunity", {"opportunity_id": "opp-101", "expected_version": 7,
                                               "next_step": "Rep concurrently scheduled legal review."})
        edited = committed_state(self.store)
        run = resume(load(self.record), self.record, self.proposer, self.executor)
        self.assertEqual(run["state"], "STALE")
        self.assertEqual(run["reason"], "VERSION_CONFLICT")
        self.assertEqual(committed_state(self.store), edited)
        self.assertEqual(edited["opportunities"]["opp-101"]["version"], 8)
        self.assertEqual(edited["opportunities"]["opp-101"]["close_date"], "2026-09-30")
        self.assertEqual(edited["opportunities"]["opp-101"]["next_step"], "Rep concurrently scheduled legal review.")
        self.assertFalse(any(event["type"] == "APPLIED" for event in edited["audit"]))
        self.assertEqual(edited["receipts"], {})

    def test_exact_digest_and_role_required(self):
        before = committed_state(self.store)
        with self.assertRaises(DomainError) as mismatch:
            self.manager.call("approve", {"proposal_id": self.run["proposal_id"], "digest": "0" * 64})
        self.assertEqual(mismatch.exception.code, "APPROVAL_DIGEST")
        with self.assertRaises(DomainError) as forbidden:
            self.proposer.call("approve", {"proposal_id": self.run["proposal_id"], "digest": self.run["proposal_digest"]})
        self.assertEqual(forbidden.exception.code, "PERMISSION_DENIED")
        self.assertEqual(committed_state(self.store), before)

    def test_edit_before_approval_is_rejected(self):
        self.operator.call("edit_opportunity", {"opportunity_id": "opp-101", "expected_version": 7, "next_step": "Rep changed the plan."})
        edited = committed_state(self.store)
        with self.assertRaises(DomainError) as stale:
            self.approve()
        self.assertEqual(stale.exception.code, "VERSION_CONFLICT")
        self.assertEqual(committed_state(self.store), edited)

    def test_forbidden_field_rejects_entire_patch(self):
        action = committed_state(self.store)["proposals"][self.run["proposal_id"]]["action"]
        args = {key: copy.deepcopy(action[key]) for key in ["run_id", "opportunity_id", "expected_version",
                "expected_evidence_revision", "expected_account_revision", "changes", "citations"]}
        args["changes"][1]["field"] = "amount_minor"
        args["citations"][1]["field"] = "amount_minor"
        before = committed_state(self.store)
        with self.assertRaises(DomainError) as forbidden:
            self.proposer.call("stage_proposal", args)
        self.assertEqual(forbidden.exception.code, "FORBIDDEN_FIELD")
        self.assertEqual(committed_state(self.store), before)

    def test_competing_approved_proposals_have_one_winner(self):
        first = self.approve()
        action = committed_state(self.store)["proposals"][self.run["proposal_id"]]["action"]
        args = {key: copy.deepcopy(action[key]) for key in ["run_id", "opportunity_id", "expected_version",
                "expected_evidence_revision", "expected_account_revision", "changes", "citations"]}
        second = self.proposer.call("stage_proposal", args)["proposal"]
        approval = self.manager.call("approve", {"proposal_id": second["id"], "digest": second["digest"]})["approval"]
        self.executor.call("apply", {"proposal_id": self.run["proposal_id"], "approval_id": first["id"],
                                     "digest": self.run["proposal_digest"]})
        winner_state = committed_state(self.store)
        with self.assertRaises(DomainError) as conflict:
            self.executor.call("apply", {"proposal_id": second["id"], "approval_id": approval["id"], "digest": second["digest"]})
        self.assertEqual(conflict.exception.code, "VERSION_CONFLICT")
        self.assertEqual(committed_state(self.store), winner_state)
        self.assertEqual(winner_state["opportunities"]["opp-101"]["version"], 8)
        self.assertEqual(sum(item["type"] == "APPLIED" for item in winner_state["audit"]), 1)
        self.assertEqual(len(winner_state["receipts"]), 1)

    def test_interactive_review_requires_exact_input_then_applies(self):
        def cli(arguments, entered=None):
            command = [sys.executable, str(ROOT / "crm.py")] + arguments
            process = subprocess.run(command, input=entered, capture_output=True, text=True, encoding="utf-8",
                                     cwd=ROOT, timeout=10, check=False)
            receipt = {"argv": command, "input_kind": "none" if entered is None else "stored_digest" if entered.strip() == self.run["proposal_digest"] else "wrong_digest",
                       "exit_code": process.returncode, "stdout": process.stdout, "stderr": process.stderr,
                       "at": datetime.now(timezone.utc).isoformat()}
            with (ROOT / "work/m1-manual-cli.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(receipt) + "\n")
            return process
        review = cli(["review", "--store", str(self.store), "--proposal", self.run["proposal_id"]])
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertIn(self.run["proposal_digest"], review.stdout)
        self.assertIn("Confirmed target close is 2026-11-12.", review.stdout)
        before = committed_state(self.store)
        approval_args = ["approve", "--store", str(self.store), "--proposal", self.run["proposal_id"],
                         "--reviewer", "manager-north", "--interactive"]
        wrong = cli(approval_args, "wrong-digest\n")
        self.assertEqual(wrong.returncode, 2)
        self.assertEqual(committed_state(self.store), before)
        accepted = cli(approval_args, self.run["proposal_digest"] + "\n")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        approved = committed_state(self.store)
        approval = next(iter(approved["approvals"].values()))
        self.assertEqual(approval["digest"], self.run["proposal_digest"])
        self.assertEqual(approval["reviewer"], "manager-north")
        self.assertEqual(approved["opportunities"], self.before["opportunities"])
        applied = cli(["resume", "--store", str(self.store), "--run", str(self.record.parent)])
        self.assertEqual(applied.returncode, 0, applied.stderr)
        final = committed_state(self.store)
        self.assertEqual(final["opportunities"]["opp-101"]["version"], 8)
        self.assertEqual(sum(item["type"] == "APPLIED" for item in final["audit"]), 1)
        receipt = next(iter(final["receipts"].values()))
        self.assertEqual(receipt["proposal_id"], self.run["proposal_id"])
        self.assertEqual(receipt["approval_id"], approval["id"])
        self.assertEqual(receipt["digest"], self.run["proposal_digest"])
        self.assertEqual(final["approvals"][approval["id"]]["state"], "CONSUMED")
