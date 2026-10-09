"""Reconstruct frozen outcomes from actual domain files; never invokes the provider or Java."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from crm_agent.protocol import strict_json
from .freeze import ROOT, MANIFEST, check, digest
from .oracle import proposal_score, final_score, fingerprint, _same_json
from .metrics import metrics, reproducibility, outcome_gate

def load_json(path, maximum=2 * 1024 * 1024):
    if path.stat().st_size > maximum:
        raise ValueError("Verification input exceeds byte cap")
    value = strict_json(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value

def resolve_manifest(expected_sha):
    current = check()
    if digest(MANIFEST) == expected_sha:
        return current
    for path in sorted((ROOT / "evaluation/freeze-history").glob("authored-v*.json")):
        if digest(path) == expected_sha:
            prior = load_json(path)
            oracle = next(item for item in prior["rules"] if item["path"] == "evaluation/oracle.py")
            if oracle["sha256"] != digest(ROOT / "evaluation/oracle.py"):
                raise ValueError("Historical oracle changed; retain original evidence without rescoring")
            return prior
    raise ValueError("Unknown frozen manifest")

def integer(value, lower=0, upper=1000000):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("Invalid integer report field")
    return value

def initial_state(fixture):
    return {**{key: fixture[key] for key in ("schema_version", "synthetic", "name", "accounts", "opportunities", "notes")},
            "policy_version": 1, "generation": 0, "proposals": {}, "approvals": {}, "receipts": {}, "operations": {}, "audit": []}

def verify_case(item, expected, label, case, directory, method_name, schema):
    if item["input_sha256"] != expected["input_sha256"] or item["label_sha256"] != expected["label_sha256"] or "error" in item:
        raise ValueError("Case failed or case hashes changed")
    if item["expected_disposition"] != label["expected_disposition"]:
        raise ValueError("Expected outcome edited in report")
    repetition = integer(item.get("repeat", 1), 1, 3)
    base = Path(method_name) / ("repeat-" + str(repetition)) / item["case_id"] if schema == 2 else Path(method_name) / item["case_id"]
    filenames = {"before": "before.json", "pre_review_state": "pre-review-state.json", "pre_review_run": "pre-review-run.json", "after": "after.json"}
    if schema == 2:
        filenames["final_run"] = "final-run.json"
        if item.get("status") != "completed":
            raise ValueError("Incomplete case status")
    expected_keys = set(filenames) | ({"committed_state"} if schema == 2 else set())
    if set(item["artifacts"]) != expected_keys:
        raise ValueError("Missing or unexpected artifact")
    loaded = {}; paths = {}
    for key, artifact in item["artifacts"].items():
        relative = Path(artifact["path"])
        target = (directory / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(directory) or not re.fullmatch("[0-9a-f]{64}", artifact["sha256"]):
            raise ValueError("Artifact path or hash invalid")
        if key in filenames and relative.as_posix() != (base / filenames[key]).as_posix():
            raise ValueError("Artifact belongs to another case/repetition")
        if key == "committed_state" and (relative.parent.as_posix() != (base / "crm").as_posix() or not re.fullmatch(r"generation-\d{6}\.json", relative.name)):
            raise ValueError("Invalid committed generation path")
        if digest(target) != artifact["sha256"]:
            raise ValueError("Artifact bytes changed")
        loaded[key] = load_json(target, 1024 * 1024); paths[key] = target
    before, staged, after = loaded["before"], loaded["pre_review_state"], loaded["after"]
    if not _same_json(before, initial_state(case["fixture"])):
        raise ValueError("Before snapshot differs from frozen input fixture")
    for candidate in (staged, after):
        if candidate.keys() != before.keys() or any(not _same_json(candidate[key], before[key]) for key in ("schema_version", "policy_version", "synthetic", "name")):
            raise ValueError("Store identity/policy shape changed")
    if any(not _same_json(staged[key], before[key]) for key in ("accounts", "notes", "opportunities")) or staged["receipts"]:
        raise ValueError("Domain changed before review")
    pre_run = loaded["pre_review_run"]
    if pre_run.get("proposal_id"):
        proposal_id = pre_run["proposal_id"]; proposal = staged["proposals"][proposal_id]
        calculated_digest = hashlib.sha256(json.dumps(proposal["action"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if pre_run.get("state") != "WAITING_APPROVAL" or len(staged["proposals"]) != 1 or not (pre_run["proposal_digest"] == proposal["digest"] == calculated_digest):
            raise ValueError("Pre-review proposal/digest binding invalid")
        if not _same_json(after["proposals"][proposal_id]["action"], proposal["action"]) or after["proposals"][proposal_id]["digest"] != proposal["digest"]:
            raise ValueError("Reviewed action changed after pre-review scoring")
    elif staged["proposals"] or pre_run.get("state") not in {"HANDOFF", "NO_CHANGE"}:
        raise ValueError("Non-proposal has invalid stage disposition")
    score = proposal_score(pre_run, staged, label); final = final_score(before, after, label, score)
    old_target = before["opportunities"][label["opportunity_id"]]
    new_target = after["opportunities"].get(label["opportunity_id"], {})
    if any(not _same_json(old_target[key], new_target.get(key)) for key in label["protected_fields"]):
        raise ValueError("Protected target JSON value/type changed")
    integer(new_target["version"], 1)
    if not _same_json(item["pre_review"], score) or not _same_json(item["final"], final) or item["fingerprint"] != fingerprint(after) or not final["authorized"]:
        raise ValueError("Reported outcomes disagree with independently inspected domain")
    if schema == 2:
        committed = loaded["committed_state"]; generation = integer(committed["generation"], 0, 499)
        if paths["committed_state"].name != f"generation-{generation:06d}.json" or paths["committed_state"] != sorted(paths["committed_state"].parent.glob("generation-*.json"))[-1] or not _same_json(committed, after):
            raise ValueError("After artifact differs from latest committed CRM generation")
        run = loaded["final_run"]
        for key in ("tool_calls", "provider_turns", "active_milliseconds"):
            if integer(item[key]) != integer(run[key]):
                raise ValueError("Observed run counter edited")
        if integer(item["repair_attempts"]) != integer(run["counters"]["errors"]):
            raise ValueError("Repair count edited")
        if pre_run.get("proposal_id") and (run.get("proposal_id") != pre_run["proposal_id"] or run.get("proposal_digest") != pre_run["proposal_digest"]):
            raise ValueError("Final run references another proposal")
        expected_state = "APPLIED" if final["agent_apply_events"] else ("REJECTED" if pre_run.get("proposal_id") else score["disposition"])
        if run["state"] != expected_state:
            raise ValueError("Final run status differs from domain state")

def verify(path):
    try:
        path = Path(path).resolve(); report = load_json(path)
        schema = integer(report["schema_version"], 1, 2); split = report["split"]; repeat = integer(report["repeat"], 1, 3)
        if split not in {"dev", "heldout"} or repeat not in {1, 3} or (split == "heldout" and (schema != 2 or repeat != 3)):
            raise ValueError("Invalid split/repetition contract")
        if report.get("complete") is not True or report.get("model_quality_evaluated") is not False or report.get("same_author_synthetic") is not True or report.get("review_mode") != "label_aware_scripted_after_pre_review_scoring":
            raise ValueError("Missing complete explicit offline report")
        frozen = resolve_manifest(report["frozen_manifest_sha256"])
        cases = {item["case_id"]: item for item in frozen["splits"][split]["cases"]}
        if integer(report["expected_cases_per_method"], 1, 32) != len(cases) or set(report["methods"]) != {"offline-rules", "latest-note"}:
            raise ValueError("Required comparison denominator/method changed")
        expected_pairs = {(case_id, repetition) for case_id in cases for repetition in range(1, repeat + 1)}
        for name, method in report["methods"].items():
            expected_mode = "offline-rules" if name == "offline-rules" else "latest-note-baseline"
            if method.get("model_id") is not None or method.get("provider_mode") != expected_mode:
                raise ValueError("Incorrect offline provider identity")
            pairs = [(item["case_id"], integer(item.get("repeat", 1), 1, repeat)) for item in method["cases"]]
            if len(pairs) != len(expected_pairs) or set(pairs) != expected_pairs:
                raise ValueError("Missing, duplicated or changed case/repetition")
            for item in method["cases"]:
                expected = cases[item["case_id"]]; input_path, label_path = ROOT / expected["input"], ROOT / expected["label"]
                if digest(input_path) != expected["input_sha256"] or digest(label_path) != expected["label_sha256"]:
                    raise ValueError("Frozen source data bytes changed")
                case, label = load_json(input_path), load_json(label_path)
                if case["case_id"] != label["case_id"] or case["case_id"] != item["case_id"]:
                    raise ValueError("Case identity changed")
                verify_case(item, expected, label, case, path.parent, name, schema)
            if not _same_json(method["metrics"], metrics(method["cases"])):
                raise ValueError("Metric numerator/denominator changed")
            if schema == 2 and not _same_json(method["reproducibility"], reproducibility(method["cases"], repeat)):
                raise ValueError("Repeat stability report changed")
        result = {"verified_methods": 2, "verified_case_runs": len(cases) * repeat * 2, "model_quality_evaluated": False}
        if schema == 2:
            gate = outcome_gate(report)
            if not _same_json(report["gate"], gate):
                raise ValueError("Acceptance gate edited")
            result["gate"] = gate
        return result
    except (KeyError, TypeError, IndexError, OSError) as error:
        raise ValueError("Malformed/missing verification evidence: " + str(error)) from error

def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--report", type=Path, required=True)
    try:
        result = verify(parser.parse_args().report); print(json.dumps(result))
        return 0 if result.get("gate", {"passed": True})["passed"] else 1
    except ValueError as error:
        print(str(error), file=sys.stderr); return 2

if __name__ == "__main__":
    raise SystemExit(main())
