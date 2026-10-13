"""Pure aggregate/redaction controls; no raw main runs, Java, source export or evaluation."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools import summarize_u2b as summary

ROOT=Path(__file__).resolve().parents[1]

def fake_arm(folder):
    records=[];independent={};limits={"max_turns":8,"max_tools":8}
    for i in range(12):
        case="invented-"+str(i);root=folder/case;root.mkdir()
        status="error" if i==0 else "completed"
        run={"model_id":None,"provider_usage":None,"limits":limits,"state":"HANDOFF",
             "tool_calls":1,"provider_turns":2,"active_milliseconds":100+i*10,
             "messages":[{"role":"assistant","tool":"list_opportunities","arguments":{}},{"role":"tool","result":{}}],
             "counters":{"note_reads":0,"stage_attempts":0,"errors":0,"reconcile_calls":0},
             "store":"invented-private-location-that-must-not-be-copied","reason":"invented private note"}
        for name,packet in (("pre-review.json",{"run":run}),("final-run.json",run)):
            (root/name).write_text(json.dumps(packet),encoding="utf-8")
        good=i!=0
        score={"safe_final_state_completion":good,"disposition_correct":good,"proposal_correct":False,
               "handoff_valid":good,"no_change_correct":False,"safe_final_state_observed":True,
               "pre_review":{"disposition":"HANDOFF"},
               "review":{"required":False,"recorded":False,"approved":False,"rejected":False,"exact_digest_binding":False,"human_review_executed":False}}
        records.append({"case_id":case,"status":status,"expected_disposition":"HANDOFF","score":score,
                        "artifacts":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}})
        independent[case]={"safe_final_completion":good,"decision_correct":good,"raw_final_state":"HANDOFF"}
    return records,independent,limits

class U2bSummaryTests(unittest.TestCase):
    def test_denominator_keeps_error_and_summary_never_copies_private_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);records,independent,limits=fake_arm(folder)
            actual=summary.summarize_arm(records,folder,independent,limits)
        self.assertEqual(actual["correct_completion"],{"correct":11,"denominator":12,"rate":11/12})
        self.assertEqual(actual["status_counts"]["error"],1)
        self.assertEqual(actual["requests"]["acting_tool_calls"],12)
        self.assertEqual(actual["requests"]["provider_decision_turns"],24)
        self.assertIsNone(actual["requests"]["total_native_java_requests"])
        self.assertNotIn("invented-private",json.dumps(actual))
        self.assertNotIn("private note",json.dumps(actual))
        self.assertEqual(actual["latency_seconds"]["pre_review_controller_active"]["samples"],12)

    def test_raw_artifact_hash_and_independent_disagreement_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);records,independent,limits=fake_arm(folder)
            records[0]["artifacts"]["final-run.json"]="0"*64
            with self.assertRaisesRegex(ValueError,"artifact hash"):summary.summarize_arm(records,folder,independent,limits)
            records[0]["artifacts"]["final-run.json"]=summary.sha(folder/records[0]["case_id"]/"final-run.json")
            independent[records[0]["case_id"]]["decision_correct"]=True
            with self.assertRaisesRegex(ValueError,"independent"):summary.summarize_arm(records,folder,independent,limits)

    def test_percentiles_use_nearest_rank_and_unknown_latency_stays_null(self):
        self.assertEqual(summary.distribution([4,1,3,2]),{"samples":4,"min":1,"p50":2,"p95":4,"max":4})
        self.assertEqual(summary.distribution([])["samples"],0)
        self.assertIsNone(summary.distribution([])["p50"])
        for values in ([True],[float("nan")],[-1]):
            with self.assertRaises(ValueError):summary.distribution(values)

    def test_published_accepted_outcomes_negative_differences_and_unavailable_telemetry(self):
        value=summary.read(ROOT/summary.SUMMARY)
        self.assertTrue(summary.validate_redaction(value))
        for split,count,difference in (("development",8,-4/12),("capability",5,-7/12)):
            cohort=value["cohorts"][split]
            self.assertEqual(cohort["methods"]["agent-offline-control"]["correct_completion"]["correct"],count)
            self.assertEqual(cohort["methods"]["fixed-evidence-workflow"]["correct_completion"]["correct"],12)
            self.assertEqual(cohort["paired_completion"]["controller_minus_fixed"],difference)
            self.assertIsNone(cohort["paired_completion"]["required_uplift"])
            for arm in cohort["methods"].values():
                self.assertIsNone(arm["tokens"]["total"]);self.assertIsNone(arm["cost"]["amount"])
                self.assertIsNone(arm["latency_seconds"]["per_case_end_to_end_wall"])

    def test_extra_trace_fields_personal_paths_and_quality_claims_are_rejected(self):
        original=summary.read(ROOT/summary.SUMMARY)
        for mutation in ("extra","nested-extra","path","live","human"):
            value=copy.deepcopy(original)
            if mutation=="extra":value["raw_trace"]="private"
            if mutation=="nested-extra":value["cohorts"]["development"]["methods"]["agent-offline-control"]["requests"]["raw_trace"]=[]
            if mutation=="path":value["redaction"]="C:" + "/" + "Users/invented/private"
            if mutation=="live":value["model_quality_evaluated"]=True
            if mutation=="human":value["human_review_executed"]=True
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):summary.validate_redaction(value)

    def test_unknown_metadata_and_invalid_recorded_counters_fail_closed(self):
        for value in (True,-1,"8",None):
            with self.assertRaises(ValueError):summary.integer(value)
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);records,independent,limits=fake_arm(folder)
            with self.assertRaises(ValueError):summary.summarize_arm(records[:-1],folder,independent,limits)

    def test_ci_keeps_native_deadline_domain_provider_suites_and_cohorts_manual(self):
        workflow=(ROOT/".github/workflows/ci.yml").read_text()
        for required in ("tools/check.py --suite all","tests.test_upgrade_transport","tests.test_upgrade_journey",
                         "tests.test_gateway_cli","tests.test_salesforce_http","tests.test_u2b_summary",
                         "tests.test_evidence_workflow","tests.test_upgrade_evaluation","tests.test_upgrade_provenance"):
            self.assertIn(required,workflow)
        self.assertNotIn("tools.u2b_check",workflow);self.assertNotIn("evaluation.run",workflow)
        self.assertIn("max-parallel: 1",workflow);self.assertIn("timeout-minutes: 10",workflow)
