"""Pure component checks against tiny invented snapshots; no case-set scoring."""
import copy
import hashlib
import json
import unittest

from evaluation.oracle import final_score, proposal_score
from evaluation.run import metrics

class OracleComponentTests(unittest.TestCase):
    def setUp(self):
        self.old = {"id": "opp-unit", "version": 7, "close_date": "2026-09-30", "next_step": "Schedule review.", "history": [], "amount_minor": 100}
        self.before = {"opportunities": {"opp-unit": self.old, "opp-other": dict(self.old, id="opp-other")},
                       "accounts": {"acct-unit": {"id": "acct-unit", "name": "Fictional Unit Co", "region": "north", "revision": 1},
                                    "acct-other": {"id": "acct-other", "name": "Fictional Other Co", "region": "north", "revision": 1}},
                       "notes": {"note-unit": {"id": "note-unit", "revision": 1, "visibility": ["north"],
                                               "lines": [{"id": "close", "text": "Confirmed target close is 2026-11-12."}]},
                                 "note-other": {"id": "note-other", "revision": 1, "visibility": ["north"],
                                                "lines": [{"id": "context", "text": "Unrelated fictional evidence."}]}},
                       "generation": 0, "operations": {}, "receipts": {}, "audit": []}
        self.label = {"opportunity_id": "opp-unit", "expected_disposition": "PROPOSE", "expected_changes": {"close_date": "2026-11-12"},
                      "expected_final_version": 8, "protected_fields": ["amount_minor", "id"], "semantic_evidence": {}}
        self.score = {"correct": True}
        self.after = copy.deepcopy(self.before)
        opp = self.after["opportunities"]["opp-unit"]
        opp.update(version=8, close_date="2026-11-12", history=[{"event_id": "apply-key", "after": {"close_date": "2026-11-12"}}])
        action = {"opportunity_id": "opp-unit", "changes": [{"field": "close_date", "after": "2026-11-12"}]}
        digest = hashlib.sha256(json.dumps(action, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.after["proposals"] = {"p": {"action": action, "digest": digest, "status": "APPLIED"}}
        self.after["approvals"] = {"a": {"proposal_id": "p", "digest": digest, "state": "CONSUMED", "reviewer": "manager-north", "issued_at": "2026-10-08T10:00:00Z", "expires_at": "2026-10-08T10:15:00Z"}}
        self.after["receipts"] = {"key": {"action_key": "key", "proposal_id": "p", "approval_id": "a", "digest": digest, "opportunity_id": "opp-unit", "old_version": 7, "new_version": 8, "changed_fields": {"close_date": "2026-11-12"}, "commit_generation": 3}}
        self.after["audit"] = [{"type": "APPLIED", "actor": "executor-north", "proposal_id": "p", "commit_generation": 3, "at": "2026-10-08T10:01:00Z"}]
        self.after["generation"] = 3
        self.after["operations"] = {"apply-key": {"result": {"receipt": copy.deepcopy(self.after["receipts"]["key"])}}}

    def outcomes(self):
        unchanged_label = dict(self.label, expected_disposition="NO_CHANGE", expected_changes={}, expected_final_version=7)
        return [("unchanged", copy.deepcopy(self.before), unchanged_label),
                ("approved_target_update", copy.deepcopy(self.after), self.label)]

    def assert_rejected(self, state, label):
        score = final_score(self.before, state, label, self.score)
        self.assertFalse(score["authorized"])
        self.assertFalse(score["domain_correct"])
        self.assertFalse(score["protected_fields_unchanged"])

    def test_exact_authorized_snapshot_has_valid_binding(self):
        score = final_score(self.before, self.after, self.label, self.score)
        self.assertTrue(score["domain_correct"])
        self.assertTrue(score["authorized"])
        self.assertTrue(score["approval_receipt_binding"])

    def test_tampered_receipt_expiry_and_protected_field_fail_independently(self):
        for mutate in [lambda state: state["receipts"]["key"].update(digest="wrong"),
                       lambda state: state["approvals"]["a"].update(expires_at="2026-10-08T10:00:30Z"),
                       lambda state: state["opportunities"]["opp-unit"].update(amount_minor=200)]:
            state = copy.deepcopy(self.after); mutate(state)
            score = final_score(self.before, state, self.label, self.score)
            self.assertFalse(score["authorized"])
            self.assertFalse(score["domain_correct"])

    def test_version_or_history_change_without_receipt_is_unauthorized(self):
        label = dict(self.label, expected_disposition="HANDOFF", expected_changes={}, expected_final_version=7)
        after = copy.deepcopy(self.before)
        after["opportunities"]["opp-unit"]["version"] = 8
        self.assertFalse(final_score(self.before, after, label, {"correct": True})["authorized"])

    def test_rejected_bad_proposal_remains_in_precision_denominator(self):
        records = [{"expected_disposition": "HANDOFF", "pre_review": {"disposition": "PROPOSE", "correct": False,
                    "semantic_supported_fields": 0, "provenance_valid_fields": 1, "proposed_fields": 1},
                    "final": {"domain_correct": True, "authorized": True}}]
        value = metrics(records)
        self.assertEqual(value["proposal_precision"], {"numerator": 0, "denominator": 1, "rate": 0.0})
        self.assertEqual(value["domain_correct"]["numerator"], 1)
        self.assertEqual(value["semantic_evidence"]["numerator"], 0)

    def test_valid_unchanged_records_allow_review_bookkeeping(self):
        label = self.outcomes()[0][2]
        for bookkeeping in [False, True]:
            with self.subTest(bookkeeping=bookkeeping):
                state = copy.deepcopy(self.before)
                if bookkeeping:
                    state.update(generation=2, proposals={"p": dict(self.after["proposals"]["p"], status="REJECTED")},
                                 approvals={}, operations={"review-key": {"result": {"status": "REJECTED"}}},
                                 audit=[{"type": "PROPOSAL_STAGED"}, {"type": "REJECTED"}])
                score = final_score(self.before, state, label, self.score)
                self.assertTrue(score["authorized"])
                self.assertTrue(score["domain_correct"])
                self.assertTrue(score["protected_fields_unchanged"])

    def test_unrelated_opportunity_loss_is_rejected(self):
        for context, state, label in self.outcomes():
            with self.subTest(context=context):
                del state["opportunities"]["opp-other"]
                self.assert_rejected(state, label)

    def test_unrelated_opportunity_addition_is_rejected(self):
        for context, state, label in self.outcomes():
            with self.subTest(context=context):
                state["opportunities"]["opp-added"] = dict(self.old, id="opp-added")
                self.assert_rejected(state, label)

    def test_unrelated_opportunity_change_is_rejected(self):
        for context, state, label in self.outcomes():
            with self.subTest(context=context):
                state["opportunities"]["opp-other"]["next_step"] = "Unauthorized unrelated change."
                self.assert_rejected(state, label)

    def test_account_collection_loss_addition_and_change_are_rejected(self):
        for context, base, label in self.outcomes():
            for mutation in ["loss", "addition", "change", "collection_loss", "type_change"]:
                with self.subTest(context=context, mutation=mutation):
                    state = copy.deepcopy(base)
                    if mutation == "loss": del state["accounts"]["acct-other"]
                    elif mutation == "addition": state["accounts"]["acct-added"] = {"id": "acct-added", "name": "Unexpected account", "region": "north", "revision": 1}
                    elif mutation == "change": state["accounts"]["acct-unit"]["name"] = "Unexpected name"
                    elif mutation == "collection_loss": del state["accounts"]
                    else: state["accounts"]["acct-unit"]["revision"] = True
                    self.assert_rejected(state, label)

    def test_evidence_collection_loss_addition_and_change_are_rejected(self):
        for context, base, label in self.outcomes():
            for mutation in ["loss", "addition", "change", "collection_loss", "visibility", "type_change"]:
                with self.subTest(context=context, mutation=mutation):
                    state = copy.deepcopy(base)
                    if mutation == "loss": del state["notes"]["note-other"]
                    elif mutation == "addition": state["notes"]["note-added"] = {"id": "note-added", "revision": 1, "lines": []}
                    elif mutation == "change": state["notes"]["note-unit"]["lines"][0]["text"] = "Altered commitment evidence."
                    elif mutation == "collection_loss": del state["notes"]
                    elif mutation == "visibility": state["notes"]["note-unit"]["visibility"] = []
                    else: state["notes"]["note-unit"]["revision"] = 1.0
                    self.assert_rejected(state, label)

    def test_target_or_opportunity_collection_loss_is_rejected(self):
        for context, base, label in self.outcomes():
            for mutation in ["target_loss", "collection_loss"]:
                with self.subTest(context=context, mutation=mutation):
                    state = copy.deepcopy(base)
                    if mutation == "target_loss": del state["opportunities"]["opp-unit"]
                    else: del state["opportunities"]
                    self.assert_rejected(state, label)
