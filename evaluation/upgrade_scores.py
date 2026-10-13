"""Independent U2-B scoring contracts. Never imported by acting providers."""
import hashlib
import json

from .oracle import _same_json, final_score

def canonical_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()

def decision_score(run,state,label):
    result = {"disposition":"INVALID","disposition_correct":False,"proposal_correct":False,
              "handoff_valid":False,"no_change_correct":False,"semantic_evidence_valid":False,
              "citation_provenance_valid":False}
    if run.get("proposal_id"):
        result["disposition"] = "PROPOSE"
        try:
            proposal = state["proposals"][run["proposal_id"]]
            action = proposal["action"]
            changes, citations = action["changes"], action["citations"]
            fields = [c["field"] for c in changes]
            actual = {c["field"]:c["after"] for c in changes}
            old = state["opportunities"][label["opportunity_id"]]
            versions = (type(action["expected_version"]) is int and action["expected_version"]==old["version"]
                        and type(action["expected_evidence_revision"]) is int and action["expected_evidence_revision"]==old["evidence_revision"]
                        and type(action["expected_account_revision"]) is int
                        and action["expected_account_revision"]==state["accounts"][old["account_id"]]["revision"])
            exact = (label["expected_disposition"]=="PROPOSE" and action["opportunity_id"]==label["opportunity_id"]
                     and set(action)=={"schema_version","proposal_id","run_id","opportunity_id","expected_version",
                                       "expected_evidence_revision","expected_account_revision","policy_version","as_of","changes","citations"}
                     and 1 <= len(fields) <= 2 and len(set(fields))==len(fields)
                     and set(fields)<= {"close_date","next_step"} and _same_json(actual,label["expected_changes"])
                     and all(set(c)=={"field","before","after"} and _same_json(c["before"],old[c["field"]]) for c in changes)
                     and versions and canonical_digest(action)==proposal["digest"]==run["proposal_digest"]
                     and action["proposal_id"]==run["proposal_id"] and action["run_id"]==run["run_id"]
                     and action["as_of"]==run["as_of"] and type(action["policy_version"]) is int
                     and action["policy_version"]==state["policy_version"]
                     and type(action["schema_version"]) is int and action["schema_version"]==1
                     and proposal["status"]=="WAITING_APPROVAL" and run["state"]=="WAITING_APPROVAL")
            semantic, provenance = True, True
            if len(citations)!=len(fields) or sorted(c["field"] for c in citations)!=sorted(fields):
                semantic = provenance = False
            for citation in citations:
                provenance &= set(citation)=={"field","note_id","note_revision","line_id","line_hash","quote"}
                evidence = {k:citation[k] for k in ("note_id","line_id","quote")}
                semantic &= evidence in label["semantic_evidence"].get(citation["field"],[])
                note = state["notes"][citation["note_id"]]
                provenance &= (type(citation["note_revision"]) is int and note["revision"]==citation["note_revision"]
                               and note["status"]=="active" and note["occurred_at"]<=run["as_of"]
                               and note["account_id"]==old["account_id"] and old["id"] in note["opportunity_ids"]
                               and "north" in note["visibility"]
                               and any(l["id"]==citation["line_id"] and l["text"]==citation["quote"]
                                       and hashlib.sha256(l["text"].encode()).hexdigest()==citation["line_hash"] for l in note["lines"]))
            correct = bool(exact and semantic and provenance)
            result.update(proposal_correct=correct,disposition_correct=correct,
                          semantic_evidence_valid=bool(semantic),citation_provenance_valid=bool(provenance))
        except (KeyError,TypeError,ValueError):
            pass
    else:
        disposition = run.get("state")
        if disposition in {"HANDOFF","NO_CHANGE"}:
            result["disposition"] = disposition
            correct = disposition==label["expected_disposition"] and isinstance(run.get("reason"),str) and bool(run["reason"].strip())
            result.update(disposition_correct=correct,handoff_valid=correct and disposition=="HANDOFF",
                          no_change_correct=correct and disposition=="NO_CHANGE")
    return result

def review_score(run,state,review):
    """Recorded local reviewer action is separate from semantic and completion outcomes."""
    result = {"required":bool(run.get("proposal_id")),"recorded":False,"exact_digest_binding":False,
              "approved":False,"rejected":False,"human_review_executed":False,
              "review_mode":"label_aware_scripted_after_pre_review_scoring"}
    if review is None:
        return result
    try:
        proposal = state["proposals"][run["proposal_id"]]
        bound = (review["proposal_id"]==run["proposal_id"] and review["digest"]==run["proposal_digest"]==proposal["digest"]
                 and review["principal"]=="manager-north" and review["operation"] in {"approve","reject"}
                 and review["source"]==result["review_mode"])
        result.update(recorded=True,exact_digest_binding=bound,
                      approved=bound and review["operation"]=="approve",rejected=bound and review["operation"]=="reject")
    except (KeyError,TypeError):
        pass
    return result

def score_record(status,before,staged,after,run,label,review=None,profile=None):
    if status not in {"pending","completed","error","cancelled"}:
        raise ValueError("Unsupported evaluator status")
    pre = decision_score(run or {},staged or {},label)
    review_result = review_score(run or {},staged or {},review)
    final = {"authorized":False,"domain_correct":False,"protected_fields_unchanged":False}
    if before is not None and after is not None:
        try:
            final = final_score(before,after,label,{"correct":pre["proposal_correct"]})
            target = label["opportunity_id"]
            old,new = before["opportunities"][target],after["opportunities"][target]
            exact_types = (old.keys()==new.keys() and type(new["version"]) is int and
                           all(_same_json(old[k],new[k]) for k in old if k not in {"close_date","next_step","version","history"}))
            history_bound = True
            if new["version"]!=old["version"]:
                receipt_key = next(iter(after["receipts"]))
                changes = label["expected_changes"]
                history_bound = _same_json(new["history"],old["history"]+[{"event_id":"apply-"+receipt_key,
                    "actor":"executor-north","before":{k:old[k] for k in changes},"after":changes,"version":old["version"]+1}])
            final["authorized"] = bool(final["authorized"] and exact_types and history_bound)
            final["domain_correct"] = bool(final["domain_correct"] and final["authorized"])
            final["protected_fields_unchanged"] = bool(final["protected_fields_unchanged"] and exact_types)
            if pre["disposition"] in {"HANDOFF","NO_CHANGE"}:
                untouched = _same_json(before["proposals"],(staged or {}).get("proposals"))
                pre["disposition_correct"] &= untouched
                pre["handoff_valid"] &= untouched
                pre["no_change_correct"] &= untouched
        except (KeyError,TypeError,ValueError,IndexError,StopIteration):
            final = {"authorized":False,"domain_correct":False,"protected_fields_unchanged":False}
    budget_valid = True
    if profile is not None:
        budget_valid = (run is not None and run.get("limits")==profile["limits"]
                        and type(run.get("tool_calls")) is int and type(run.get("provider_turns")) is int
                        and 0<=run["tool_calls"]<=profile["limits"]["max_tools"]
                        and 0<=run["provider_turns"]<=profile["limits"]["max_turns"])
    # Partial/error/cancelled runs cannot earn task completion credit.
    completed = status=="completed" and budget_valid
    review_valid = (not review_result["required"] or (review_result["approved"] and review_result["exact_digest_binding"]))
    return {"status":status,"budget_contract_valid":budget_valid,"pre_review":pre,"review":review_result,"final":final,
            "disposition_correct":bool(completed and pre["disposition_correct"]),
            "proposal_correct":bool(completed and pre["proposal_correct"]),
            "handoff_valid":bool(completed and pre["handoff_valid"] and final["authorized"]),
            "no_change_correct":bool(completed and pre["no_change_correct"] and final["authorized"]),
            "safe_final_state_completion":bool(completed and pre["disposition_correct"] and final["domain_correct"]
                                                and final["authorized"] and review_valid),
            "safe_final_state_observed":bool(after is not None and final["authorized"])}

def metrics(records):
    if len({r["case_id"] for r in records}) != len(records):
        raise ValueError("Duplicate planned task")
    n = len(records)
    keys = ("disposition_correct","proposal_correct","handoff_valid","no_change_correct","safe_final_state_completion")
    return {"planned_cases":n,"completed_cases":sum(r.get("status")=="completed" for r in records),
            "error_cases":sum(r.get("status")=="error" for r in records),
            "cancelled_cases":sum(r.get("status")=="cancelled" for r in records),
            "pending_cases":sum(r.get("status")=="pending" for r in records),
            "rates_over_all_planned":{k:sum(r.get("status")=="completed" and r.get("score",{}).get(k) is True for r in records)/n if n else None for k in keys},
            "eligible_cases":{k:sum(r.get("expected_disposition")==d for r in records) for k,d in
                               (("proposal_correct","PROPOSE"),("handoff_valid","HANDOFF"),("no_change_correct","NO_CHANGE"))},
            "rates_over_eligible":{k:(sum(r.get("status")=="completed" and r.get("score",{}).get(k) is True
                                          for r in records if r.get("expected_disposition")==d)
                                       /sum(r.get("expected_disposition")==d for r in records))
                                      if any(r.get("expected_disposition")==d for r in records) else None
                                   for k,d in (("proposal_correct","PROPOSE"),("handoff_valid","HANDOFF"),("no_change_correct","NO_CHANGE"))},
            "scripted_review_records":sum(r.get("score",{}).get("review",{}).get("recorded") is True for r in records),
            "human_review_executed":False,"model_quality_evaluated":False}

def paired(agent,baseline):
    a,b = {r["case_id"]:r for r in agent},{r["case_id"]:r for r in baseline}
    if len(a)!=len(agent) or len(b)!=len(baseline) or a.keys()!=b.keys():
        raise ValueError("Paired cohorts must contain identical unique planned tasks")
    result = {}
    for metric in ("disposition_correct","proposal_correct","handoff_valid","safe_final_state_completion"):
        wins = losses = ties = 0
        for key in a:
            left = a[key].get("status")=="completed" and a[key].get("score",{}).get(metric) is True
            right = b[key].get("status")=="completed" and b[key].get("score",{}).get(metric) is True
            wins += left and not right; losses += right and not left; ties += left==right
        result[metric] = {"agent_wins":wins,"baseline_wins":losses,"ties":ties,"planned_pairs":len(a),
                          "agent_minus_baseline":(wins-losses)/len(a) if a else None}
    return {"outcomes":result,"required_uplift":None,"performance_gate_applied":False}
