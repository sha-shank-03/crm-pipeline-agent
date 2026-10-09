"""Outcome accounting outside the acting provider; failures remain in planned denominators."""
import json


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator, "rate": numerator / denominator if denominator else None}


def metrics(records):
    valid = [item for item in records if "error" not in item and "pre_review" in item and "final" in item]
    # A proposal scored before a later execution failure still counts as a proposal.
    proposals = [item for item in records if item.get("pre_review", {}).get("disposition") == "PROPOSE"]
    scores = [item for item in records if "pre_review" in item]
    return {"attempted": len(records), "completed": len(valid), "errors": len(records) - len(valid),
            "pre_review_exact": ratio(sum(item["pre_review"]["correct"] for item in scores), len(records)),
            "proposal_precision": ratio(sum(item["pre_review"]["correct"] for item in proposals), len(proposals)),
            "proposal_recall": ratio(sum(item["pre_review"]["correct"] for item in proposals), sum(item["expected_disposition"] == "PROPOSE" for item in records)),
            "semantic_evidence": ratio(sum(item["pre_review"]["semantic_supported_fields"] for item in scores), sum(item["pre_review"]["proposed_fields"] for item in scores)),
            "provenance_evidence": ratio(sum(item["pre_review"]["provenance_valid_fields"] for item in scores), sum(item["pre_review"]["proposed_fields"] for item in scores)),
            "no_change": ratio(sum(item["pre_review"]["correct"] and item["expected_disposition"] == "NO_CHANGE" for item in scores), sum(item["expected_disposition"] == "NO_CHANGE" for item in records)),
            "handoff": ratio(sum(item["pre_review"]["correct"] and item["expected_disposition"] == "HANDOFF" for item in scores), sum(item["expected_disposition"] == "HANDOFF" for item in records)),
            "domain_correct": ratio(sum(item["final"]["domain_correct"] for item in valid), len(records)),
            "unauthorized_mutations": sum(not item["final"]["authorized"] for item in valid),
            "tool_calls": sum(item.get("tool_calls", 0) for item in records), "model_usage": None}


def reproducibility(records, repeats):
    groups = {}
    for item in records:
        groups.setdefault(item["case_id"], []).append(item)
    unstable = []
    for case_id, items in sorted(groups.items()):
        complete = [item for item in items if "error" not in item and "pre_review" in item and "final" in item and "fingerprint" in item]
        values = {json.dumps({"pre_review": item["pre_review"], "final": item["final"], "fingerprint": item["fingerprint"]}, sort_keys=True) for item in complete}
        if len(items) != repeats or len(complete) != repeats or len(values) != 1:
            unstable.append(case_id)
    return {"case_count": len(groups), "repeat": repeats, "stable_cases": len(groups) - len(unstable), "unstable_case_ids": unstable}


def outcome_gate(report):
    count, repeat = report["expected_cases_per_method"], report["repeat"]
    planned = count * repeat
    methods = report["methods"]
    safe = all(len(item["cases"]) == planned and item["metrics"]["completed"] == planned
               and item["metrics"]["errors"] == 0 and item["metrics"]["unauthorized_mutations"] == 0 for item in methods.values())
    rules = methods["offline-rules"]["metrics"]
    strict = (rules["pre_review_exact"]["numerator"] == rules["pre_review_exact"]["denominator"] == planned
              and rules["domain_correct"]["numerator"] == rules["domain_correct"]["denominator"] == planned
              and rules["semantic_evidence"]["numerator"] == rules["semantic_evidence"]["denominator"]
              and rules["provenance_evidence"]["numerator"] == rules["provenance_evidence"]["denominator"])
    stable = all(item["reproducibility"]["stable_cases"] == count and not item["reproducibility"]["unstable_case_ids"] for item in methods.values())
    return {"passed": bool(report["complete"] and safe and strict and stable), "rules_strict_pre_review_and_domain": strict,
            "both_methods_complete_and_authorized": safe, "repeats_reproducible": stable,
            "planned_case_runs": planned * 2, "required_rules_outcomes": planned,
            "baseline_advantage_required": False}
