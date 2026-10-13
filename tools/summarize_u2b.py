"""Read accepted offline evidence and emit aggregate-only JSON; never execute CRM/evaluation."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

METHODS = ("agent-offline-control","fixed-evidence-workflow")
SPLITS = ("development","capability")
TOOLS = ("list_opportunities","get_opportunity","list_notes","read_note","stage_proposal")
STATES = ("APPLIED","REJECTED","HANDOFF","NO_CHANGE")
SUMMARY = "docs/u2b-offline-summary.json"

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid recorded counter")
    return value

def distribution(values):
    if not values:
        return {"samples":0,"min":None,"p50":None,"p95":None,"max":None}
    if any(type(v) not in {int,float} or not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Invalid latency sample")
    values = sorted(values)
    return {"samples":len(values),"min":values[0],"p50":values[math.ceil(.5*len(values))-1],
            "p95":values[math.ceil(.95*len(values))-1],"max":values[-1]}

def rate(count,total):
    return {"correct":count,"denominator":total,"rate":count/total if total else None}

def verify_artifacts(folder,artifacts):
    folder = Path(folder).resolve()
    for relative,wanted in artifacts.items():
        if not isinstance(relative,str) or "\\" in relative or ":" in relative:
            raise ValueError("Invalid private artifact path")
        path = folder/relative
        if path.is_symlink() or not path.resolve().is_relative_to(folder) or sha(path)!=wanted:
            raise ValueError("Private artifact hash differs")

def summarize_arm(records,folder,independent,limits):
    if len(records)!=12 or len({r["case_id"] for r in records})!=12:
        raise ValueError("Expected unique complete planned denominator")
    counts = Counter()
    dispositions = Counter()
    states = Counter()
    tool_counts = Counter()
    eligible = Counter()
    pre_latency,final_latency = [],[]
    counter_totals = Counter()
    for record in records:
        case = record["case_id"]
        root = Path(folder)/case
        verify_artifacts(root,record["artifacts"])
        score = record["score"]
        counts[record["status"]] += 1
        if record["status"] not in {"completed","error","cancelled","pending"}:
            raise ValueError("Unexpected case status")
        pre = read(root/"pre-review.json")["run"]
        final = read(root/"final-run.json")
        if (pre["model_id"] is not None or final["model_id"] is not None
                or pre["provider_usage"] is not None or final["provider_usage"] is not None
                or pre["limits"]!=limits or final["limits"]!=limits):
            raise ValueError("Offline mode, usage or equal budget differs")
        if final["state"] not in STATES or score["pre_review"]["disposition"] not in {"PROPOSE","HANDOFF","NO_CHANGE","INVALID"}:
            raise ValueError("Unexpected terminal state/disposition")
        accepted = independent[case]
        if (accepted["safe_final_completion"] is not score["safe_final_state_completion"]
                or accepted["decision_correct"] is not score["disposition_correct"]
                or accepted["raw_final_state"]!=final["state"]):
            raise ValueError("Score differs from main independent case review")
        for metric in ("safe_final_state_completion","proposal_correct","handoff_valid","no_change_correct","disposition_correct"):
            counts[metric] += record["status"]=="completed" and score[metric] is True
        counts["safe_state_observed"] += score["safe_final_state_observed"] is True
        for k in ("required","recorded","approved","rejected","exact_digest_binding"):
            counts["review_"+k] += score["review"][k] is True
        if score["review"]["human_review_executed"] is not False:
            raise ValueError("Actual human review cannot be claimed by this offline summary")
        eligible[record["expected_disposition"]] += 1
        dispositions[score["pre_review"]["disposition"]] += 1
        states[final["state"]] += 1
        calls = [m["tool"] for m in pre["messages"] if m.get("role")=="assistant" and "tool" in m]
        if any(t not in TOOLS for t in calls) or len(calls)!=integer(pre["tool_calls"]):
            raise ValueError("Acting tool counts differ")
        tool_counts.update(calls)
        counter_totals["provider_turns"] += integer(pre["provider_turns"])
        counter_totals["acting_tool_calls"] += len(calls)
        for key in ("note_reads","stage_attempts","errors","reconcile_calls"):
            counter_totals[key] += integer(final["counters"][key])
        pre_ms,final_ms = integer(pre["active_milliseconds"]),integer(final["active_milliseconds"])
        if final_ms<pre_ms:
            raise ValueError("Final active time decreased")
        pre_latency.append(pre_ms/1000);final_latency.append(final_ms/1000)
    return {"planned_arm_runs":len(records),"status_counts":{k:counts[k] for k in ("completed","error","cancelled","pending")},
            "correct_completion":rate(counts["safe_final_state_completion"],len(records)),
            "correct_decision":rate(counts["disposition_correct"],len(records)),
            "correct_proposal":rate(counts["proposal_correct"],eligible["PROPOSE"]),
            "valid_handoff":rate(counts["handoff_valid"],eligible["HANDOFF"]),
            "correct_no_change":rate(counts["no_change_correct"],eligible["NO_CHANGE"]),
            "safe_final_state_observed":rate(counts["safe_state_observed"],len(records)),
            "pre_review_disposition_counts":{k:dispositions[k] for k in ("PROPOSE","HANDOFF","NO_CHANGE","INVALID")},
            "final_state_counts":{k:states[k] for k in STATES},
            "scripted_review":{k:counts["review_"+k] for k in ("required","recorded","approved","rejected","exact_digest_binding")},
            "requests":{"acting_tool_calls":counter_totals["acting_tool_calls"],
                        "acting_tool_counts":{t:tool_counts[t] for t in TOOLS},
                        "provider_decision_turns":counter_totals["provider_turns"],
                        "note_reads":counter_totals["note_reads"],"stage_attempts":counter_totals["stage_attempts"],
                        "recorded_tool_errors":counter_totals["errors"],"resume_dispatch_counter":counter_totals["reconcile_calls"],
                        "total_native_java_requests":None,"model_inference_requests":0,"salesforce_api_requests":0},
            "latency_seconds":{"pre_review_controller_active":distribution(pre_latency),
                               "controller_plus_resume_active":distribution(final_latency),
                               "per_case_end_to_end_wall":None},
            "tokens":{"input":None,"output":None,"total":None,"availability":"not_applicable_no_inference"},
            "cost":{"amount":None,"currency":None,"availability":"no_provider_billing_record_no_inference"}}

def build(review_path,execution_path,roots,predecessor_path):
    review,execution = read(review_path),read(execution_path)
    if (review["outcome"]!="passed_offline_evaluation_contract" or review["final_upgrade_acceptance"] is not False
            or review["unique_tasks"]!=24 or review["arm_executions"]!=48 or execution["live_calls"]!=0
            or set(roots)!=set(SPLITS) or not all(c["passed"] is True for c in review["checks"])
            or not all(c["exit_code"]==0 for c in execution["commands"])):
        raise ValueError("Expected accepted offline main evidence")
    predecessor = read(predecessor_path)
    if predecessor["version"]!="upgrade-v4-u2b-source":
        raise ValueError("Expected sealed U2-B source version")
    summary={"schema_version":1,"version":"u2c-offline-summary-v1","evidence_date":"2026-10-12",
             "execution_mode":"main_executed_local_java_offline","scope":{"unique_worker_authored_exposed_tasks":24,
             "planned_arm_runs":48,"cohorts":2,"runs_per_task_per_arm":1,"blind":False,
             "scenario_families_paired_between_cohorts":True},
             "review_mode":"label_aware_scripted_after_pre_review_scoring","human_review_executed":False,
             "model_quality_evaluated":False,"salesforce_integration_evaluated":False,
             "main_acceptance":"offline_evaluation_contract_only","full_upgrade_accepted":False,
             "main_independent_assertions":integer(review["independent_assertions"]),
             "main_negative_controls":integer(review["negative_controls"]),"cohorts":{},
             "source_evidence_sha256":{"main_independent_review":sha(review_path),"main_execution_receipt":sha(execution_path),
                                      "sealed_u2b_manifest":sha(predecessor_path)},
             "definitions":{"correct_completion":"Correct pre-review decision plus expected final domain state and authorization/receipt/history binding; all planned arm runs in denominator.",
              "correct_proposal":"Exact supported proposal with current versions and field-specific semantic/provenance evidence; denominator expected PROPOSE tasks.",
              "valid_handoff":"Expected HANDOFF, nonempty reason, no concealed stage and authorized unchanged domain; denominator expected HANDOFF tasks.",
              "scripted_review":"Label-aware approve/reject after pre-review scoring; required/recorded/bound actions counted separately, never human review.",
              "safe_state_observed":"Authorized final state even if the task decision was wrong; distinct from correct completion.",
              "latency":"Saved active_milliseconds/1000. Pre-review is acting controller; final adds resume time. Neither includes fixture init, manager approval action or complete evaluator/file I/O.",
              "percentiles":"Nearest-rank p50/p95 over all 12 arm runs in a cohort; one host/run, descriptive only.",
              "request_counts":"Acting tool dispatches/decision turns from saved transcript/counters; resume counter is separate. Complete native subprocess attempt trace was not persisted, so total Java requests is null.",
              "tokens_cost":"No LLM inference; tokens not applicable and no provider billing/currency record. Null is not a measured zero charge.",
              "cohort_wall":"Report/command elapsed seconds cover both arms and evaluator overhead; unavailable per-arm end-to-end time is not inferred.",
              "differences":"Controller minus fixed-workflow correct completions over 12 paired tasks; negative differences retained; no uplift threshold."},
             "redaction":"Aggregate whitelist only; no raw paths, case IDs, notes, prompts, traces, credentials or operator identifiers copied.",
             "remaining_gates":["Actual secured live provider and main-held unseen cohort","Genuine isolated Salesforce sandbox",
                                "Actual human review where claimed","Current cross-platform/hosted CI and packaging","Final publication"]}
    for split in SPLITS:
        root = Path(roots[split])
        report=read(root/"report.json")
        if (report["split"]!=split or report["complete"] is not True or report["blind"] is not False
                or report["exposure"]!="worker-authored/exposed" or report["execution_mode"]!="local_java_offline_control"
                or report["human_review_executed"] is not False or report["model_quality_evaluated"] is not False
                or report["live_model_calls"]!=0 or report["live_salesforce_calls"]!=0 or set(report["methods"])!=set(METHODS)):
            raise ValueError("Offline report exposure/mode differs")
        arms={}
        for method in METHODS:
            independent={c["case_id"]:c for c in review["cases"] if c["split"]==split and c["method"]==method}
            arms[method]=summarize_arm(report["methods"][method]["cases"],root/method,independent,report["profile"]["limits"])
            expected=review["summary"][split][method]
            if arms[method]["correct_completion"]["correct"]!=expected["complete_correct"] or expected["planned"]!=12:
                raise ValueError("Aggregate differs from accepted main result")
        paired=report["paired"]["outcomes"]["safe_final_state_completion"]
        # Copy only numeric comparison counters and verify the accepted difference.
        difference=(arms[METHODS[0]]["correct_completion"]["correct"]-arms[METHODS[1]]["correct_completion"]["correct"])/12
        if paired["agent_minus_baseline"]!=difference:
            raise ValueError("Paired completion difference differs")
        command=next(c for c in execution["commands"] if "--split" in c["argv"] and c["argv"][c["argv"].index("--split")+1]==split)
        summary["cohorts"][split]={"methods":arms,"paired_completion":{"controller_wins":integer(paired["agent_wins"]),
            "fixed_workflow_wins":integer(paired["baseline_wins"]),"ties":integer(paired["ties"]),
            "planned_pairs":12,"controller_minus_fixed":difference,"required_uplift":None},
            "equal_limits":report["profile"]["limits"],"both_arms_report_elapsed_seconds":report["elapsed_seconds"],
            "both_arms_command_wall_seconds":round(command["seconds"],6)}
        summary["source_evidence_sha256"][split+"_report"]=sha(root/"report.json")
    validate_redaction(summary)
    return summary

def validate_redaction(value):
    """Enforce aggregate schema as well as recognizable path/trace hygiene."""
    fields={"schema_version","version","evidence_date","execution_mode","scope","review_mode",
            "human_review_executed","model_quality_evaluated","salesforce_integration_evaluated",
            "main_acceptance","full_upgrade_accepted","main_independent_assertions","main_negative_controls",
            "cohorts","source_evidence_sha256","definitions","redaction","remaining_gates"}
    arm_fields={"planned_arm_runs","status_counts","correct_completion","correct_decision","correct_proposal",
                "valid_handoff","correct_no_change","safe_final_state_observed","pre_review_disposition_counts",
                "final_state_counts","scripted_review","requests","latency_seconds","tokens","cost"}
    request_fields={"acting_tool_calls","acting_tool_counts","provider_decision_turns","note_reads","stage_attempts",
                    "recorded_tool_errors","resume_dispatch_counter","total_native_java_requests","model_inference_requests","salesforce_api_requests"}
    if not isinstance(value,dict) or set(value)!=fields or set(value["cohorts"])!=set(SPLITS):
        raise ValueError("Invalid public summary")
    for cohort in value["cohorts"].values():
        if set(cohort)!={"methods","paired_completion","equal_limits","both_arms_report_elapsed_seconds","both_arms_command_wall_seconds"} or set(cohort["methods"])!=set(METHODS):
            raise ValueError("Unknown public cohort field")
        for arm in cohort["methods"].values():
            if set(arm)!=arm_fields or set(arm["requests"])!=request_fields or set(arm["requests"]["acting_tool_counts"])!=set(TOOLS):
                raise ValueError("Unknown public arm/request field")
    if not all(isinstance(h,str) and re.fullmatch("[0-9a-f]{64}",h) for h in value["source_evidence_sha256"].values()):
        raise ValueError("Invalid evidence hash")
    encoded=json.dumps(value,ensure_ascii=False)
    if re.search(r"[A-Za-z]:[\\/]|/(?:Users|home)/|(?:\\\\|//)|\b(?:opp-|note-|acct-|u2b-dev-|u2b-cap-)|-----BEGIN|(?:ghp_|sk-proj-)",encoded):
        raise ValueError("Raw identifier/path/secret-like material in public summary")
    if value.get("model_quality_evaluated") is not False or value.get("human_review_executed") is not False:
        raise ValueError("Unsupported quality/review claim")
    return True

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("main-review","execution-summary","development-root","capability-root","predecessor-manifest","out"):
        parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args()
    result=build(args.main_review,args.execution_summary,{"development":args.development_root,"capability":args.capability_root},args.predecessor_manifest)
    # Exact reviewed local evidence can be regenerated; no raw inputs are modified.
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"summary_written":True,"unique_tasks":24,"arm_runs":48,"new_evaluation_executed":False}))
if __name__ == "__main__":
    main()
