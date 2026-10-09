"""Pure tests on tiny invented reports; no authored case-set execution or provider/Java calls."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from evaluation.metrics import metrics, reproducibility, outcome_gate
from evaluation.oracle import proposal_score, final_score, fingerprint
from evaluation.verify import initial_state, verify
from evaluation.run import execute
from tests import test_m2_oracle as oracle_units

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

class EvaluationEvidenceTests(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[1] / "work"; work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=work); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.output = self.root / "runs"
        toy = oracle_units.OracleComponentTests(); toy.setUp()
        fixture = {"schema_version": 1, "synthetic": True, "name": "Invented verifier unit",
                   **{key: copy.deepcopy(toy.before[key]) for key in ("opportunities", "accounts", "notes")}}
        before = initial_state(fixture)
        citation = {"field": "close_date", "note_id": "note-unit", "note_revision": 1, "line_id": "close",
                    "quote": fixture["notes"]["note-unit"]["lines"][0]["text"]}
        citation["line_hash"] = hashlib.sha256(citation["quote"].encode()).hexdigest()
        self.bank = []; self.materials = {}
        for case_id, disposition in [("unit-propose", "PROPOSE"), ("unit-no-change", "NO_CHANGE"), ("unit-handoff", "HANDOFF")]:
            label = dict(copy.deepcopy(toy.label), case_id=case_id, expected_disposition=disposition)
            if disposition == "PROPOSE":
                label["semantic_evidence"] = {"close_date": [{key: citation[key] for key in ("note_id", "line_id", "quote")}]}
            else:
                label.update(expected_changes={}, expected_final_version=7)
            case = {"case_id": case_id, "fixture": copy.deepcopy(fixture), "as_of": "2026-10-08", "task": {"opportunity_id": "opp-unit"}}
            input_path, label_path = Path("inputs") / (case_id + ".json"), Path("labels") / (case_id + ".json")
            save(self.root / input_path, case); save(self.root / label_path, label)
            self.bank.append({"case_id": case_id, "input": input_path.as_posix(), "label": label_path.as_posix(),
                              "input_sha256": digest(self.root / input_path), "label_sha256": digest(self.root / label_path)})
            staged = copy.deepcopy(before); after = copy.deepcopy(before)
            pre_run = {"state": disposition, "proposal_id": None, "tool_calls": 2, "provider_turns": 2, "active_milliseconds": 20, "counters": {"errors": 0}}
            if disposition == "PROPOSE":
                action = copy.deepcopy(toy.after["proposals"]["p"]["action"]); action["citations"] = [copy.deepcopy(citation)]
                action["changes"][0]["before"] = fixture["opportunities"]["opp-unit"]["close_date"]
                action_digest = hashlib.sha256(json.dumps(action, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                staged["proposals"] = {"p": {"action": action, "digest": action_digest, "status": "STAGED"}}
                staged["generation"] = 1
                after = copy.deepcopy(toy.after); after.update({key: before[key] for key in ("schema_version", "synthetic", "name", "policy_version")})
                after["proposals"]["p"].update(action=action, digest=action_digest)
                after["approvals"]["a"]["digest"] = action_digest; after["receipts"]["key"]["digest"] = action_digest
                pre_run.update(state="WAITING_APPROVAL", proposal_id="p", proposal_digest=action_digest)
            final_run = dict(copy.deepcopy(pre_run), state="APPLIED" if disposition == "PROPOSE" else disposition)
            self.materials[case_id] = (case, label, before, staged, pre_run, after, final_run)
        self.frozen = {"splits": {"dev": {"cases": self.bank, "case_count": 3}}}
        self.report = {"schema_version": 2, "split": "dev", "repeat": 3, "complete": True,
                       "model_quality_evaluated": False, "same_author_synthetic": True,
                       "review_mode": "label_aware_scripted_after_pre_review_scoring", "frozen_manifest_sha256": "unit-frozen",
                       "expected_cases_per_method": 3, "methods": {}}
        for method, mode in [("offline-rules", "offline-rules"), ("latest-note", "latest-note-baseline")]:
            records = []
            for repetition in range(1, 4):
                for expected in self.bank:
                    case, label, old, staged, pre_run, after, final_run = self.materials[expected["case_id"]]
                    base = Path(method) / ("repeat-" + str(repetition)) / case["case_id"]
                    values = {"before": (base / "before.json", old), "pre_review_state": (base / "pre-review-state.json", staged),
                              "pre_review_run": (base / "pre-review-run.json", pre_run), "after": (base / "after.json", after),
                              "final_run": (base / "final-run.json", final_run),
                              "committed_state": (base / "crm" / f"generation-{after['generation']:06d}.json", after)}
                    artifacts = {}
                    for key, (path, value) in values.items():
                        save(self.output / path, value); artifacts[key] = {"path": path.as_posix(), "sha256": digest(self.output / path)}
                    score = proposal_score(pre_run, staged, label)
                    records.append({"case_id": case["case_id"], "repeat": repetition, "status": "completed", "expected_disposition": label["expected_disposition"],
                                    "input_sha256": expected["input_sha256"], "label_sha256": expected["label_sha256"],
                                    "pre_review": score, "final": final_score(old, after, label, score), "fingerprint": fingerprint(after),
                                    "artifacts": artifacts, "tool_calls": 2, "provider_turns": 2, "active_milliseconds": 20, "repair_attempts": 0})
            self.report["methods"][method] = {"provider_mode": mode, "model_id": None, "cases": records,
                                              "metrics": metrics(records), "reproducibility": reproducibility(records, 3)}
        self.report["gate"] = outcome_gate(self.report)
        self.report_path = self.output / "report.json"; save(self.report_path, self.report)
        self.original_bytes = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.addCleanup(patch.stopall)
        patch("evaluation.verify.ROOT", self.root).start()
        patch("evaluation.verify.resolve_manifest", return_value=self.frozen).start()
        patch("evaluation.run.JavaClient", side_effect=AssertionError("Pure tests must not call Java")).start()

    def restore(self):
        for path, raw in self.original_bytes.items():
            path.write_bytes(raw)

    def mutate_artifact(self, report, key, mutation, refresh=True):
        record = report["methods"]["offline-rules"]["cases"][0]
        artifact = record["artifacts"][key]; path = self.output / artifact["path"]
        value = json.loads(path.read_text()); mutation(value); save(path, value)
        if refresh:
            artifact["sha256"] = digest(path)

    def rejects(self, report):
        save(self.report_path, report)
        with self.assertRaises(ValueError):
            verify(self.report_path)

    def test_valid_approved_unchanged_and_handoff_all_three_repeats(self):
        result = verify(self.report_path)
        self.assertEqual(result["verified_case_runs"], 18); self.assertTrue(result["gate"]["passed"])

    def test_archived_schema_one_development_report_still_verifies(self):
        report = copy.deepcopy(self.report); report.update(schema_version=1, repeat=1); report.pop("gate")
        for name, method in report["methods"].items():
            method["cases"] = [item for item in method["cases"] if item["repeat"] == 1]
            for item in method["cases"]:
                item["artifacts"] = {key: artifact for key, artifact in item["artifacts"].items() if key in {"before", "pre_review_state", "pre_review_run", "after"}}
                for artifact in item["artifacts"].values():
                    path = self.output / artifact["path"]; relative = Path(name) / item["case_id"] / path.name
                    target = self.output / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(path.read_bytes())
                    artifact["path"] = relative.as_posix()
            method["metrics"] = metrics(method["cases"]); method.pop("reproducibility")
        save(self.report_path, report)
        self.assertEqual(verify(self.report_path)["verified_case_runs"], 6)

    def test_rehashed_protected_target_number_to_boolean_is_rejected(self):
        report = copy.deepcopy(self.report)
        self.mutate_artifact(report, "after", lambda value: value["opportunities"]["opp-unit"].update(amount_minor=True))
        self.rejects(report)

    def test_isolated_report_controls_reject_all_and_keep_source_unchanged(self):
        from evaluation.corruptions import run
        result = run(self.report_path, self.root / "corruptions")
        self.assertEqual(result["total"], 18); self.assertEqual(result["rejected"], 18)
        self.assertTrue(result["source_report_unchanged"])
        self.assertTrue(verify(self.report_path)["gate"]["passed"])

    def test_loss_duplicate_repeat_and_method_removal_rejected(self):
        mutations = [lambda r: r["methods"]["offline-rules"]["cases"].pop(),
                     lambda r: r["methods"]["offline-rules"]["cases"].__setitem__(1, copy.deepcopy(r["methods"]["offline-rules"]["cases"][0])),
                     lambda r: r["methods"].pop("latest-note"), lambda r: r.update(repeat=True),
                     lambda r: r["methods"]["offline-rules"]["cases"][0].update(repeat=True),
                     lambda r: r.update(expected_cases_per_method=2)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                report = copy.deepcopy(self.report); mutation(report); self.rejects(report)

    def test_wrong_expected_outcome_or_model_identity_rejected(self):
        for mutation in [lambda r: r["methods"]["offline-rules"]["cases"][0].update(expected_disposition="HANDOFF"),
                         lambda r: r["methods"]["offline-rules"].update(model_id="pretend-model"),
                         lambda r: r["methods"]["latest-note"].update(provider_mode="offline-rules")]:
            report = copy.deepcopy(self.report); mutation(report); self.rejects(report)

    def test_artifact_hash_and_collection_corruptions_even_if_rehashed(self):
        for key, mutation, refresh in [
            ("after", lambda v: v["accounts"].pop("acct-other"), False),
            ("after", lambda v: v["opportunities"].pop("opp-other"), True),
            ("after", lambda v: v["notes"]["note-other"].update(visibility=[]), True),
            ("after", lambda v: v["receipts"]["key"].update(digest="wrong"), True),
            ("after", lambda v: v["approvals"]["a"].update(expires_at="2026-10-08T10:00:30Z"), True),
            ("after", lambda v: v["opportunities"]["opp-unit"].update(amount_minor=200), True),
            ("before", lambda v: v["opportunities"]["opp-other"].update(amount_minor=300), True),
            ("pre_review_state", lambda v: v["opportunities"]["opp-unit"].update(version=8), True),
            ("pre_review_run", lambda v: v.update(proposal_digest="wrong"), True)]:
            with self.subTest(key=key, mutation=mutation):
                self.restore(); report = copy.deepcopy(self.report); self.mutate_artifact(report, key, mutation, refresh); self.rejects(report)

    def test_committed_state_latest_generation_and_counter_corruption_rejected(self):
        for key, mutation in [("committed_state", lambda v: v["opportunities"]["opp-unit"].update(close_date="2026-12-01")),
                              ("final_run", lambda v: v.update(tool_calls=3)),
                              ("final_run", lambda v: v.update(state="WAITING_APPROVAL"))]:
            self.restore(); report = copy.deepcopy(self.report); self.mutate_artifact(report, key, mutation); self.rejects(report)
        self.restore()
        artifact = self.report["methods"]["offline-rules"]["cases"][0]["artifacts"]["committed_state"]
        save((self.output / artifact["path"]).parent / "generation-000004.json", self.materials["unit-propose"][5])
        self.rejects(copy.deepcopy(self.report))

    def test_missing_artifact_escaped_path_and_cross_case_alias_rejected(self):
        for mutation in [lambda a: a.pop("committed_state"), lambda a: a["after"].update(path="../../outside.json"),
                         lambda a: a["after"].update(path="latest-note/repeat-1/unit-propose/after.json")]:
            report = copy.deepcopy(self.report); mutation(report["methods"]["offline-rules"]["cases"][0]["artifacts"]); self.rejects(report)

    def test_metric_boolean_rate_and_gate_forgery_rejected(self):
        for mutation in [lambda r: r["methods"]["offline-rules"]["metrics"]["pre_review_exact"].update(numerator=8),
                         lambda r: r["methods"]["offline-rules"]["metrics"]["proposal_precision"].update(rate=True),
                         lambda r: r["methods"]["offline-rules"]["reproducibility"].update(stable_cases=2),
                         lambda r: r["gate"].update(passed=False)]:
            report = copy.deepcopy(self.report); mutation(report); self.rejects(report)

    def test_partial_report_case_error_and_source_hash_change_rejected(self):
        for mutation in [lambda r: r.update(complete=False), lambda r: r["methods"]["offline-rules"]["cases"][0].update(error={"type": "Timeout"})]:
            report = copy.deepcopy(self.report); mutation(report); self.rejects(report)
        path = self.root / self.bank[0]["input"]; path.write_text("{}")
        self.rejects(copy.deepcopy(self.report))

    def test_heldout_execution_flag_and_repeat_gate_prevent_java_calls(self):
        for options in [{"split": "heldout", "repeat": 3}, {"split": "heldout", "repeat": 1, "allow_heldout": True}, {"split": "heldout", "repeat": True, "allow_heldout": True}]:
            with self.assertRaises(ValueError): execute(self.root / "never-created", **options)
        self.assertFalse((self.root / "never-created").exists())

class AccountingTests(unittest.TestCase):
    def record(self, correct=True, error=False):
        value = {"case_id": "invented", "repeat": 1, "expected_disposition": "PROPOSE", "pre_review": {"disposition": "PROPOSE", "correct": correct, "proposed_fields": 1, "semantic_supported_fields": int(correct), "provenance_valid_fields": 1},
                 "final": {"domain_correct": True, "authorized": True}, "fingerprint": "stable"}
        if error: value["error"] = {"type": "OwnedTimeout"}
        return value

    def test_failed_and_not_executed_cases_remain_in_denominators(self):
        result = metrics([self.record(error=True), {"case_id": "not-executed", "expected_disposition": "PROPOSE", "error": {"type": "Budget"}}])
        self.assertEqual(result["completed"], 0); self.assertEqual(result["errors"], 2)
        self.assertEqual(result["proposal_precision"]["denominator"], 1)
        self.assertEqual(result["proposal_recall"]["denominator"], 2)
        self.assertEqual(result["domain_correct"]["denominator"], 2)

    def test_reviewer_cannot_hide_incorrect_pre_review_but_baseline_need_not_win(self):
        rules = [self.record()]; baseline = [self.record(correct=False)]
        report = {"expected_cases_per_method": 1, "repeat": 1, "complete": True, "methods": {name: {"cases": rows, "metrics": metrics(rows), "reproducibility": reproducibility(rows, 1)} for name, rows in [("offline-rules", rules), ("latest-note", baseline)]}}
        self.assertTrue(outcome_gate(report)["passed"])
        report["methods"]["offline-rules"]["cases"] = baseline
        report["methods"]["offline-rules"]["metrics"] = metrics(baseline)
        self.assertFalse(outcome_gate(report)["passed"])

    def test_repeat_failure_or_different_fingerprint_is_visible(self):
        records = [dict(self.record(), repeat=number) for number in range(1, 4)]
        self.assertEqual(reproducibility(records, 3)["stable_cases"], 1)
        records[-1]["fingerprint"] = "different"
        self.assertEqual(reproducibility(records, 3)["unstable_case_ids"], ["invented"])
        records[-1] = self.record(error=True)
        self.assertEqual(reproducibility(records, 3)["stable_cases"], 0)
