"""Real-Java policy failures; execute only under the M2 integration lease."""
import copy
import hashlib
import json
import uuid

from crm_agent.java_client import JavaClient
from crm_agent.protocol import strict_json
from tests.m2_support import M2Fixture, committed_state

class M2PolicyTests(M2Fixture):
    def test_note_revision_changed_before_approval(self):
        note = copy.deepcopy(self.fixture["notes"]["note-101"])
        note.update(revision=2)
        note["lines"][0]["text"] = "Confirmed target close is 2026-12-01."
        self.change({"kind": "note", "note": note})
        self.unchanged_error(self.manager, "approve", {"proposal_id": self.run["proposal_id"], "digest": self.run["proposal_digest"]}, "EVIDENCE_CHANGED")

    def test_new_uncited_evidence_invalidates_approval(self):
        self.approve()
        note = copy.deepcopy(self.fixture["notes"]["note-101"])
        note.update(id="note-102", occurred_at="2026-10-07")
        note["lines"][0]["text"] = "Confirmed target close is 2026-12-01."
        self.change({"kind": "note", "note": note})
        self.assertEqual(committed_state(self.store)["opportunities"]["opp-101"]["evidence_revision"], 2)
        self.unchanged_error(self.executor, "apply", self.apply_args, "EVIDENCE_CHANGED")

    def test_revoked_note_invalidates_apply_and_cached_stage(self):
        self.approve()
        note = copy.deepcopy(self.fixture["notes"]["note-101"])
        note.update(revision=2, visibility=[])
        self.change({"kind": "note", "note": note})
        self.unchanged_error(self.executor, "apply", self.apply_args, "EVIDENCE_CHANGED")
        original_key = self.run["proposal_id"][2:]
        self.unchanged_error(self.proposer, "stage_proposal", self.stage_args, "PERMISSION_DENIED", original_key)
        self.unchanged_error(self.proposer, "operation_result", {"operation": "stage_proposal", "request_id": original_key, "args": self.stage_args}, "PERMISSION_DENIED")

    def test_revoked_executor_blocks_apply_and_receipt(self):
        self.approve()
        key = str(uuid.uuid4())
        self.executor.call("apply", self.apply_args, key)
        self.change({"kind": "access", "principal": "executor-north", "enabled": False})
        self.unchanged_error(self.executor, "apply", self.apply_args, "PERMISSION_DENIED", key)
        self.unchanged_error(self.executor, "receipt", {"action_key": key}, "PERMISSION_DENIED")

    def test_revoked_manager_blocks_unapplied_approval(self):
        self.approve()
        self.change({"kind": "access", "principal": "manager-north", "enabled": False})
        self.unchanged_error(self.executor, "apply", self.apply_args, "PERMISSION_DENIED")

    def test_account_scope_changed_after_approval(self):
        self.approve()
        self.change({"kind": "account", "account_id": "acct-101", "region": "south"})
        self.unchanged_error(self.executor, "apply", self.apply_args, "PERMISSION_DENIED")

    def test_wrong_scope_manager_and_note_read(self):
        south = JavaClient(self.store, "manager-south")
        self.unchanged_error(south, "approve", {"proposal_id": self.run["proposal_id"], "digest": self.run["proposal_digest"]}, "PERMISSION_DENIED")
        self.unchanged_error(JavaClient(self.store, "proposer-south"), "read_note", {"note_id": "note-101"}, "PERMISSION_DENIED")

    def test_policy_version_changed_after_review(self):
        self.approve()
        self.change({"kind": "policy", "version": 2})
        self.unchanged_error(self.executor, "apply", self.apply_args, "POLICY_VERSION")

    def test_expired_approval_is_unchanged(self):
        self.approve()
        self.change({"kind": "expire_approval", "approval_id": self.approval["id"]})
        self.unchanged_error(self.executor, "apply", self.apply_args, "APPROVAL_EXPIRED")

    def test_successful_duplicate_survives_consumed_expired_approval(self):
        self.approve()
        key = str(uuid.uuid4())
        first = self.executor.call("apply", self.apply_args, key)
        self.change({"kind": "expire_approval", "approval_id": self.approval["id"]})
        before = committed_state(self.store)
        self.assertEqual(self.executor.call("apply", self.apply_args, key), first)
        self.assertEqual(committed_state(self.store), before)
        self.unchanged_error(self.executor, "apply", self.apply_args, "ALREADY_APPLIED")

    def test_missing_and_rejected_approval(self):
        args = {"proposal_id": self.run["proposal_id"], "approval_id": "missing", "digest": self.run["proposal_digest"]}
        self.unchanged_error(self.executor, "apply", args, "NOT_FOUND")
        self.manager.call("reject", {"proposal_id": self.run["proposal_id"], "digest": self.run["proposal_digest"]})
        self.unchanged_error(self.manager, "approve", {"proposal_id": self.run["proposal_id"], "digest": self.run["proposal_digest"]}, "APPROVAL_STATE")

    def test_stage_approve_apply_request_collisions(self):
        stage_key = self.run["proposal_id"][2:]
        changed = copy.deepcopy(self.stage_args)
        changed["run_id"] = "different-run"
        self.unchanged_error(self.proposer, "stage_proposal", changed, "IDEMPOTENCY_CONFLICT", stage_key)
        self.approve()
        approve_key = self.approval["id"][2:]
        self.unchanged_error(self.manager, "approve", {"proposal_id": self.run["proposal_id"], "digest": "0" * 64}, "IDEMPOTENCY_CONFLICT", approve_key)
        apply_key = str(uuid.uuid4())
        self.executor.call("apply", self.apply_args, apply_key)
        self.unchanged_error(self.executor, "apply", dict(self.apply_args, digest="0" * 64), "IDEMPOTENCY_CONFLICT", apply_key)

    def test_duplicate_stage_and_approve_return_original(self):
        before = committed_state(self.store)
        first = self.proposer.call("stage_proposal", self.stage_args, self.run["proposal_id"][2:])
        self.assertEqual(first["proposal"]["digest"], self.run["proposal_digest"])
        self.assertEqual(committed_state(self.store), before)
        approval = self.approve()
        before = committed_state(self.store)
        second = self.manager.call("approve", {"proposal_id": self.run["proposal_id"], "digest": self.run["proposal_digest"]}, approval["id"][2:])
        self.assertEqual(second["approval"], approval)
        self.assertEqual(committed_state(self.store), before)

    def test_invalid_patch_shapes_are_atomic(self):
        variants = []
        for value, code in [(None, "INVALID_REQUEST"), (True, "INVALID_REQUEST"), ("2026-02-30", "INVALID_DATE"), ("2027-12-01", "INVALID_PATCH")]:
            args = copy.deepcopy(self.stage_args); args["changes"][0]["after"] = value; variants.append((args, code))
        args = copy.deepcopy(self.stage_args); args["unexpected"] = True; variants.append((args, "INVALID_REQUEST"))
        args = copy.deepcopy(self.stage_args); args["expected_version"] = True; variants.append((args, "INVALID_REQUEST"))
        args = copy.deepcopy(self.stage_args); args["changes"].append(copy.deepcopy(args["changes"][0])); variants.append((args, "INVALID_PATCH"))
        args = copy.deepcopy(self.stage_args); args["changes"][0]["before"] = "2026-09-29"; variants.append((args, "VERSION_CONFLICT"))
        args = copy.deepcopy(self.stage_args); args["changes"][1]["after"] = "x" * 241; variants.append((args, "INVALID_EVIDENCE"))
        for args, code in variants:
            with self.subTest(code=code): self.unchanged_error(self.proposer, "stage_proposal", args, code)

    def test_fabricated_wrong_link_future_and_superseded_evidence(self):
        for field in ("quote", "line_hash", "line_id"):
            args = copy.deepcopy(self.stage_args); args["citations"][0][field] = "fabricated"
            self.unchanged_error(self.proposer, "stage_proposal", args, "INVALID_EVIDENCE")
        for change in [{"status": "superseded"}, {"occurred_at": "2026-10-09"}, {"opportunity_ids": []}]:
            note = copy.deepcopy(self.fixture["notes"]["note-101"])
            note.update(change, revision=committed_state(self.store)["notes"]["note-101"]["revision"] + 1)
            self.change({"kind": "note", "note": note})
            args = copy.deepcopy(self.stage_args)
            args["expected_evidence_revision"] = committed_state(self.store)["opportunities"]["opp-101"]["evidence_revision"]
            for citation in args["citations"]: citation["note_revision"] = note["revision"]
            self.unchanged_error(self.proposer, "stage_proposal", args, "INVALID_EVIDENCE")

    def test_invalid_wire_input_has_no_generation(self):
        before = committed_state(self.store)
        payloads = [(b'{"schema_version":1,"schema_version":1}', "INVALID_JSON"), (b'\xff', "INVALID_JSON"),
                    (b'{"schema_version":2,"request_id":"q","operation":"inspect","args":{}}', "SCHEMA_VERSION"),
                    (b' ' * 65537, "REQUEST_TOO_LARGE"), (b'{"n":1.5}', "INVALID_JSON")]
        payloads += [(b'{"x":"\\ud800"}', "INVALID_JSON")]
        for payload, code in payloads:
            response = strict_json(self.raw(payload).stdout.decode())
            self.assertEqual(response["error"]["code"], code)
            self.assertEqual(committed_state(self.store), before)

    def test_unicode_exact_evidence_and_canonical_digest(self):
        note = copy.deepcopy(self.fixture["notes"]["note-101"])
        note["lines"][1]["text"] = "Send the café résumé to 東京."
        note["revision"] = 2
        self.change({"kind": "note", "note": note})
        args = copy.deepcopy(self.stage_args); args["expected_evidence_revision"] = 2
        for citation in args["citations"]: citation["note_revision"] = 2
        args["changes"][1]["after"] = note["lines"][1]["text"]
        args["citations"][1].update(quote=note["lines"][1]["text"], line_hash=hashlib.sha256(note["lines"][1]["text"].encode()).hexdigest())
        proposal = self.proposer.call("stage_proposal", args)["proposal"]
        self.assertEqual(proposal["digest"], hashlib.sha256(json.dumps(proposal["action"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest())
        approval = self.manager.call("approve", {"proposal_id": proposal["id"], "digest": proposal["digest"]})["approval"]
        self.executor.call("apply", {"proposal_id": proposal["id"], "approval_id": approval["id"], "digest": proposal["digest"]})
        self.assertEqual(committed_state(self.store)["opportunities"]["opp-101"]["next_step"], "Send the café résumé to 東京.")

    def test_closed_record_and_wrong_account_evidence_reject_at_java_boundary(self):
        for variant, code in [("closed", "CLOSED_OPPORTUNITY"), ("wrong-account", "INVALID_EVIDENCE")]:
            fixture = copy.deepcopy(self.fixture)
            if variant == "closed": fixture["opportunities"]["opp-101"]["stage"] = "won"
            else:
                fixture["accounts"]["acct-202"] = {"id": "acct-202", "name": "Fictional Northstar Tools", "region": "north", "revision": 1}
                fixture["notes"]["note-101"]["account_id"] = "acct-202"
            self.store = self.output / variant
            JavaClient(self.store, "fixture-operator").call("init", {"fixture": fixture})
            self.unchanged_error(JavaClient(self.store, "proposer-north"), "stage_proposal", self.stage_args, code)

    def test_tampered_approval_digest_and_state_reject_without_apply(self):
        self.approve()
        latest = sorted(self.store.glob("generation-*.json"))[-1]
        original = committed_state(self.store)
        for change, code in [({"digest": "0" * 64}, "APPROVAL_DIGEST"), ({"state": "REJECTED"}, "APPROVAL_STATE"), ({"region": "south"}, "PERMISSION_DENIED")]:
            changed = copy.deepcopy(original)
            changed["approvals"][self.approval["id"]].update(change)
            latest.write_text(json.dumps(changed), encoding="utf-8")
            self.unchanged_error(self.executor, "apply", self.apply_args, code)

    def test_request_key_binds_as_of_date(self):
        changed_clock = JavaClient(self.store, "proposer-north", "2026-10-09")
        self.unchanged_error(changed_clock, "stage_proposal", self.stage_args, "IDEMPOTENCY_CONFLICT", self.run["proposal_id"][2:])
