"""Pure authored packet/double controls. Not a corpus or model quality evaluation."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.upgrade_cases import check, actor_payload, validate
from evaluation.upgrade_scores import canonical_digest, decision_score, score_record, metrics, paired
from tools.author_upgrade_tasks import make_case

def packets():
    case,label=make_case("development",0)
    before=copy.deepcopy(case["fixture"])
    before.update(proposals={},approvals={},receipts={},audit=[],policy_version=1)
    staged=copy.deepcopy(before)
    target=label["opportunity_id"];old=before["opportunities"][target]
    action={"schema_version":1,"proposal_id":"p-test","run_id":"run-test","opportunity_id":target,
            "expected_version":7,"expected_evidence_revision":1,"expected_account_revision":1,
            "policy_version":1,"as_of":case["as_of"],
            "changes":[{"field":f,"before":old[f],"after":v} for f,v in label["expected_changes"].items()],
            "citations":[]}
    for field,evidence in label["semantic_evidence"].items():
        e=evidence[0];n=before["notes"][e["note_id"]]
        action["citations"].append(dict(field=field,note_revision=n["revision"],line_hash=hashlib.sha256(e["quote"].encode()).hexdigest(),**e))
    digest=canonical_digest(action)
    staged["proposals"]["p-test"]={"id":"p-test","action":action,"digest":digest,"status":"WAITING_APPROVAL"}
    run={"state":"WAITING_APPROVAL","run_id":"run-test","as_of":case["as_of"],"proposal_id":"p-test","proposal_digest":digest}
    review={"proposal_id":"p-test","digest":digest,"principal":"manager-north","operation":"approve",
            "source":"label_aware_scripted_after_pre_review_scoring"}
    after=copy.deepcopy(staged)
    after["proposals"]["p-test"]["status"]="APPLIED"
    after["opportunities"][target].update(label["expected_changes"]);after["opportunities"][target]["version"]=8
    after["opportunities"][target]["history"].append({"event_id":"apply-action-test","actor":"executor-north",
            "before":{k:old[k] for k in label["expected_changes"]},"after":label["expected_changes"],"version":8})
    after["approvals"]["approval-test"]={"proposal_id":"p-test","digest":digest,"state":"CONSUMED","reviewer":"manager-north",
                                       "issued_at":"2026-10-12T10:00:00Z","expires_at":"2026-10-12T11:00:00Z"}
    after["receipts"]["action-test"]={"action_key":"action-test","proposal_id":"p-test","approval_id":"approval-test",
        "opportunity_id":target,"old_version":7,"new_version":8,"changed_fields":label["expected_changes"],"digest":digest,"commit_generation":3}
    after["audit"].append({"type":"APPLIED","actor":"executor-north","proposal_id":"p-test","commit_generation":3,"at":"2026-10-12T10:05:00Z"})
    return before,staged,after,run,label,review

class UpgradeEvaluationTests(unittest.TestCase):
    def test_all_new_hashes_unique_split_counts_and_exposure_are_explicit(self):
        registry=check()
        self.assertFalse(registry["blind"] or registry["model_quality_evaluated"])
        self.assertEqual(sum(s["case_count"] for s in registry["splits"].values()),24)
        self.assertEqual(registry["retained_regression"]["case_count"],32)
        self.assertEqual(registry["license"],"CC0-1.0")
        self.assertEqual(len({c["input_sha256"] for s in registry["splits"].values() for c in s["cases"]}),24)

    def test_actor_projection_excludes_case_labels_and_registry_and_returns_copy(self):
        case,label=make_case("development",0)
        payload=actor_payload(case)
        self.assertEqual(set(payload),{"task","as_of","fixture"})
        payload["fixture"]["notes"].clear()
        self.assertTrue(case["fixture"]["notes"])
        case["expected_disposition"]=label["expected_disposition"]
        with self.assertRaises(ValueError):actor_payload(case)

    def test_label_quote_must_exist_in_authored_input(self):
        case,label=make_case("development",0)
        label["semantic_evidence"]["next_step"][0]["quote"]="Invented absent quote."
        with self.assertRaisesRegex(ValueError,"quote absent"):validate(case,label,{"case_id":case["case_id"],"family":label["family"]})

    def test_proposal_review_and_authorized_completion_are_separate(self):
        before,staged,after,run,label,review=packets()
        score=score_record("completed",before,staged,after,run,label,review)
        self.assertTrue(score["proposal_correct"] and score["safe_final_state_completion"])
        self.assertTrue(score["review"]["exact_digest_binding"])
        self.assertFalse(score["review"]["human_review_executed"])
        without_review=score_record("completed",before,staged,after,run,label)
        self.assertTrue(without_review["proposal_correct"])
        self.assertFalse(without_review["safe_final_state_completion"])

    def test_duplicate_or_wrong_field_evidence_cannot_get_semantic_credit(self):
        _,staged,_,run,label,_=packets()
        action=staged["proposals"]["p-test"]["action"]
        action["citations"][1]=copy.deepcopy(action["citations"][0])
        digest=canonical_digest(action);staged["proposals"]["p-test"]["digest"]=digest;run["proposal_digest"]=digest
        score=decision_score(run,staged,label)
        self.assertFalse(score["proposal_correct"] or score["semantic_evidence_valid"])

    def test_hash_revision_before_value_and_digest_corruptions_fail_independently(self):
        for kind in ("hash","revision","before","digest","bool-version"):
            _,staged,_,run,label,_=packets()
            action=staged["proposals"]["p-test"]["action"]
            if kind=="hash":action["citations"][0]["line_hash"]="0"*64
            if kind=="revision":action["citations"][0]["note_revision"]=2
            if kind=="before":action["changes"][0]["before"]="incorrect"
            if kind=="bool-version":action["expected_account_revision"]=True
            if kind!="digest":
                staged["proposals"]["p-test"]["digest"]=run["proposal_digest"]=canonical_digest(action)
            else:run["proposal_digest"]="0"*64
            with self.subTest(kind=kind):self.assertFalse(decision_score(run,staged,label)["proposal_correct"])

    def test_forged_or_expired_receipt_protected_record_edit_and_wrong_review_fail_completion(self):
        for kind in ("receipt","expired","other-record","review"):
            before,staged,after,run,label,review=packets()
            if kind=="receipt":after["receipts"]["action-test"]["digest"]="0"*64
            if kind=="expired":after["approvals"]["approval-test"]["expires_at"]="2026-10-12T10:04:00Z"
            if kind=="other-record":
                sibling=next(k for k in after["opportunities"] if k!=label["opportunity_id"])
                after["opportunities"][sibling]["amount_minor"]+=1
            if kind=="review":review["digest"]="0"*64
            with self.subTest(kind=kind):
                score=score_record("completed",before,staged,after,run,label,review)
                self.assertTrue(score["proposal_correct"]);self.assertFalse(score["safe_final_state_completion"])

    def test_valid_handoff_does_not_claim_a_proposal_or_human_review(self):
        case,label=make_case("development",4)
        before=case["fixture"];before.update(proposals={},approvals={},receipts={},audit=[],policy_version=1)
        run={"state":"HANDOFF","reason":"Active commitments disagree","proposal_id":None}
        score=score_record("completed",before,before,copy.deepcopy(before),run,label)
        self.assertTrue(score["handoff_valid"] and score["safe_final_state_completion"])
        self.assertFalse(score["proposal_correct"] or score["review"]["recorded"])
        run["reason"]=""
        self.assertFalse(score_record("completed",before,before,before,run,label)["handoff_valid"])

    def test_errors_and_cancelled_safe_states_never_earn_completion_or_disappear(self):
        before,staged,after,run,label,review=packets()
        for status in ("error","cancelled","pending"):
            score=score_record(status,before,staged,after,run,label,review)
            self.assertFalse(score["safe_final_state_completion"] or score["disposition_correct"])
            self.assertTrue(score["safe_final_state_observed"])
        records=[{"case_id":"a","status":"completed","expected_disposition":"PROPOSE","score":{"proposal_correct":True}},
                 {"case_id":"b","status":"error","expected_disposition":"PROPOSE","score":{"proposal_correct":True}},
                 {"case_id":"c","status":"cancelled","expected_disposition":"HANDOFF"},
                 {"case_id":"d","status":"pending","expected_disposition":"NO_CHANGE"}]
        result=metrics(records)
        self.assertEqual(result["planned_cases"],4)
        self.assertEqual(result["rates_over_all_planned"]["proposal_correct"],0.25)
        self.assertEqual(result["rates_over_eligible"]["proposal_correct"],0.5)
        self.assertEqual((result["error_cases"],result["cancelled_cases"],result["pending_cases"]),(1,1,1))

    def test_unequal_budget_cannot_earn_completion(self):
        before,staged,after,run,label,review=packets()
        profile={"limits":{"max_tools":8,"max_turns":8}}
        run.update(limits={"max_tools":9,"max_turns":8},tool_calls=5,provider_turns=5)
        self.assertFalse(score_record("completed",before,staged,after,run,label,review,profile)["budget_contract_valid"])

    def test_typed_protected_values_and_complete_history_binding_are_required(self):
        for kind in ("bool-protected","history-actor","history-before"):
            before,staged,after,run,label,review=packets()
            target=label["opportunity_id"]
            if kind=="bool-protected":after["opportunities"][target]["evidence_revision"]=True
            if kind=="history-actor":after["opportunities"][target]["history"][-1]["actor"]="proposer-north"
            if kind=="history-before":after["opportunities"][target]["history"][-1]["before"]={}
            with self.subTest(kind=kind):
                self.assertFalse(score_record("completed",before,staged,after,run,label,review)["safe_final_state_completion"])

    def test_handoff_cannot_hide_a_staged_proposal(self):
        case,label=make_case("development",4)
        before=case["fixture"];before.update(proposals={},approvals={},receipts={},audit=[],policy_version=1)
        staged=copy.deepcopy(before);staged["proposals"]["hidden"]={"status":"WAITING_APPROVAL"}
        run={"state":"HANDOFF","reason":"Unresolved evidence","proposal_id":None}
        score=score_record("completed",before,staged,staged,run,label)
        self.assertFalse(score["handoff_valid"] or score["safe_final_state_completion"])

    def test_baseline_wins_and_negative_difference_are_retained_and_pairs_are_complete(self):
        a=[{"case_id":"a","status":"error","score":{"disposition_correct":True}},
           {"case_id":"b","status":"completed","score":{"disposition_correct":False}}]
        b=[{"case_id":"a","status":"completed","score":{"disposition_correct":True}},
           {"case_id":"b","status":"completed","score":{"disposition_correct":False}}]
        result=paired(a,b)
        self.assertEqual(result["outcomes"]["disposition_correct"]["agent_minus_baseline"],-0.5)
        self.assertEqual(result["outcomes"]["disposition_correct"]["baseline_wins"],1)
        self.assertIsNone(result["required_uplift"])
        with self.assertRaises(ValueError):paired(a,b[:1])
        with self.assertRaises(ValueError):metrics(a+[a[0]])
