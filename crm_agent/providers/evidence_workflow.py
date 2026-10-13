"""Fixed evidence workflow. No labels, evaluator imports, model or external I/O."""
from datetime import date, timedelta
import re

from ..provider import Terminal, ToolCall

DATE = re.compile(r"(?:Confirmed target close is|Customer confirmed closing on|Buyer approved a close date of) ([0-9]{4}-[0-9]{2}-[0-9]{2})\.")
DATED_RELATIVE = re.compile(r"Customer confirmed close (?:next Friday|in two weeks), explicitly dated ([0-9]{4}-[0-9]{2}-[0-9]{2})\.")
NEXT = re.compile(r"(?:Send|Schedule|Provide|Prepare|Confirm|Complete|Review|Deliver|Arrange|Share) [^\n]{1,220}\.")
REPLACE = re.compile(r"Replaces prior note (note-[A-Za-z0-9-]+)\.")
UNSAFE = re.compile(r"\b(?:ignore|system|developer|credential|password|token|secret|approve|approval|executor|export|policy|bypass|instruction)\b", re.I)
RELATIVE = re.compile(r"\b(?:next Friday|in two weeks)\b", re.I)


class EvidenceWorkflowProvider:
    """Read all eligible evidence, resolve explicit replacements, then stage or hand off."""
    mode = "fixed-evidence-workflow"
    model_id = None

    def next(self, messages, tools, limits):
        if any(m.get("role") == "tool" and "error" in m for m in messages):
            return Terminal("HANDOFF", "Tool error needs operator inspection")
        observations = [m for m in messages if m.get("role") == "tool" and "result" in m]
        observed = {m["tool"]: m["result"] for m in observations}
        if "list_opportunities" not in observed:
            return ToolCall("list_opportunities", {})
        task = " ".join(m.get("content", "") for m in messages if m.get("role") == "user")
        ids = set(re.findall(r"\bopp-[A-Za-z0-9-]+\b", task))
        candidates = [o for o in observed["list_opportunities"]["opportunities"] if o["id"] in ids]
        if len(ids) != 1 or len(candidates) != 1:
            return Terminal("HANDOFF", "A single authorized stable opportunity ID is required")
        target = candidates[0]["id"]
        if "get_opportunity" not in observed:
            return ToolCall("get_opportunity", {"id": target, "include_history": True})
        opportunity = observed["get_opportunity"]
        if opportunity["stage"] != "open":
            return Terminal("NO_CHANGE", "Closed opportunity is ineligible for edits")
        if "list_notes" not in observed:
            return ToolCall("list_notes", {"opportunity_id": target})
        as_of = observed["list_opportunities"]["as_of"]
        eligible = [n for n in observed["list_notes"]["notes"]
                    if n["status"] == "active" and n["occurred_at"] <= as_of
                    and n["account_id"] == opportunity["account_id"] and target in n["opportunity_ids"]]
        # A generic account commitment cannot select one of several linked deals.
        if any(n["opportunity_ids"] != [target] for n in eligible):
            return Terminal("HANDOFF", "Evidence links several opportunities; deal-specific clarification is required")
        # Reserve the final stage/terminal turn. Both methods receive the same budget.
        capacity = min(limits["max_note_reads"], limits["max_tools"] - 4, limits["max_turns"] - 4)
        if not eligible or len(eligible) > capacity:
            return Terminal("HANDOFF", "No current scoped evidence or insufficient complete-read budget")
        eligible.sort(key=lambda n: (n["occurred_at"], n["id"]), reverse=True)
        read = {m["result"]["id"]: m["result"] for m in observations if m["tool"] == "read_note"}
        for note in eligible:
            if note["id"] not in read:
                return ToolCall("read_note", {"note_id": note["id"]})
        active = {n["id"]: read[n["id"]] for n in eligible}
        replaced = set()
        for note in active.values():
            for line in note["lines"]:
                match = REPLACE.fullmatch(line["text"])
                if match:
                    prior = active.get(match[1])
                    if prior is None or prior["occurred_at"] >= note["occurred_at"]:
                        return Terminal("HANDOFF", "Replacement reference is absent or not earlier")
                    replaced.add(prior["id"])
        support = {"close_date": [], "next_step": []}
        unresolved_relative = False
        business_date = date.fromisoformat(as_of)
        for meta in eligible:
            note = active[meta["id"]]
            if note["id"] in replaced:
                continue
            for line in note["lines"]:
                text = line["text"]
                if UNSAFE.search(text):
                    continue
                match = DATE.fullmatch(text) or DATED_RELATIVE.fullmatch(text)
                if match:
                    try:
                        closing = date.fromisoformat(match[1])
                    except ValueError:
                        continue
                    if business_date <= closing <= business_date + timedelta(days=365):
                        support["close_date"].append((match[1], note, line))
                elif RELATIVE.search(text):
                    unresolved_relative = True
                elif NEXT.fullmatch(text):
                    support["next_step"].append((text, note, line))
        if unresolved_relative or any(len({v for v, _, _ in items}) > 1 for items in support.values()):
            return Terminal("HANDOFF", "Date ambiguity or unresolved active commitments require clarification")
        if opportunity["close_date"] < as_of and not support["close_date"]:
            return Terminal("HANDOFF", "Stale close date lacks a supported dated replacement")
        if not any(support.values()):
            return Terminal("HANDOFF", "No customer business commitment is supported")
        changes, citations = [], []
        for field in ("close_date", "next_step"):
            if not support[field] or (field == "close_date" and opportunity[field] >= as_of):
                continue
            value, note, line = support[field][0]
            if value == opportunity[field]:
                continue
            changes.append({"field": field, "before": opportunity[field], "after": value})
            citations.append({"field": field, "note_id": note["id"], "note_revision": note["revision"],
                              "line_id": line["id"], "line_hash": line["line_hash"], "quote": line["text"]})
        if not changes:
            return Terminal("NO_CHANGE", "Current fields match the supported eligible commitments")
        return ToolCall("stage_proposal", {"opportunity_id": target, "expected_version": opportunity["version"],
                        "expected_evidence_revision": opportunity["evidence_revision"],
                        "expected_account_revision": opportunity["account_revision"],
                        "changes": changes, "citations": citations})
