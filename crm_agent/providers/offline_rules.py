"""Frozen conservative rules for authored English commitments; not an LLM."""
from datetime import date, timedelta
import re

from ..provider import Terminal, ToolCall

CLOSE = re.compile(r"Confirmed target close is ([0-9]{4}-[0-9]{2}-[0-9]{2})\.")
NEXT = re.compile(r"(?:Send|Schedule|Provide|Prepare|Confirm|Complete|Review) [^\n]{1,220}\.")


def commitments(notes, as_of):
    result = {"close_date": [], "next_step": []}
    business_date = date.fromisoformat(as_of)
    for note in notes:
        for line in note["lines"]:
            text = line["text"]
            close = CLOSE.fullmatch(text)
            if close:
                try:
                    value = date.fromisoformat(close[1])
                except ValueError:
                    continue
                if business_date <= value <= business_date + timedelta(days=365):
                    result["close_date"].append((close[1], note, line))
            elif NEXT.fullmatch(text):
                result["next_step"].append((text, note, line))
    return result


class OfflineRuleProvider:
    mode = "offline-rules"
    model_id = None
    latest_only = False

    def next(self, messages, tools, limits):
        observations = [message for message in messages if message.get("role") == "tool" and "result" in message]
        observed = {message["tool"]: message["result"] for message in observations}
        if "list_opportunities" not in observed:
            return ToolCall("list_opportunities", {})
        opportunities = observed["list_opportunities"]["opportunities"]
        task = " ".join(message.get("content", "") for message in messages if message.get("role") == "user")
        requested = re.findall(r"\bopp-[A-Za-z0-9-]+\b", task)
        candidates = [item for item in opportunities if not requested or item["id"] in requested]
        if not candidates:
            return Terminal("HANDOFF", "No authorized unambiguous opportunity is available")
        if len(candidates) != 1 or len(set(requested)) > 1:
            return Terminal("HANDOFF", "Use one stable opportunity ID; names are ambiguous")
        target = candidates[0]["id"]
        if "get_opportunity" not in observed:
            return ToolCall("get_opportunity", {"id": target, "include_history": True})
        opportunity = observed["get_opportunity"]
        if opportunity["stage"] != "open":
            return Terminal("NO_CHANGE", "Closed opportunity is ineligible for edits")
        if "list_notes" not in observed:
            return ToolCall("list_notes", {"opportunity_id": target})
        as_of = observed["list_opportunities"]["as_of"]
        notes = [item for item in observed["list_notes"]["notes"]
                 if item["status"] == "active" and item["occurred_at"] <= as_of
                 and item["account_id"] == opportunity["account_id"] and target in item["opportunity_ids"]]
        notes.sort(key=lambda item: (item["occurred_at"], item["id"]), reverse=True)
        if self.latest_only:
            notes = notes[:1]
        if not notes or len(notes) > limits["max_note_reads"]:
            return Terminal("HANDOFF", "Insufficient evidence or evidence read budget")
        read_notes = {message["result"]["id"]: message["result"] for message in observations if message["tool"] == "read_note"}
        for note in notes:
            if note["id"] not in read_notes:
                return ToolCall("read_note", {"note_id": note["id"]})
        support = commitments([read_notes[item["id"]] for item in notes], as_of)
        for matches in support.values():
            if len({value for value, _, _ in matches}) > 1:
                return Terminal("HANDOFF", "Active commitment evidence conflicts")
        stale = opportunity["close_date"] < as_of
        if stale and not support["close_date"]:
            return Terminal("HANDOFF", "Stale close date has no supported replacement")
        changes, citations = [], []
        for field in ("close_date", "next_step"):
            if not support[field] or (field == "close_date" and not stale):
                continue
            value, note, line = support[field][0]
            if value == opportunity[field]:
                continue
            changes.append({"field": field, "before": opportunity[field], "after": value})
            citations.append({"field": field, "note_id": note["id"], "note_revision": note["revision"],
                              "line_id": line["id"], "line_hash": line["line_hash"], "quote": line["text"]})
        if not changes and not support["next_step"] and not support["close_date"]:
            return Terminal("HANDOFF", "No supported commitment in the bounded grammar")
        if not changes:
            return Terminal("NO_CHANGE", "Present fields match supported commitments")
        return ToolCall("stage_proposal", {"opportunity_id": target, "expected_version": opportunity["version"],
                        "expected_evidence_revision": opportunity["evidence_revision"],
                        "expected_account_revision": opportunity["account_revision"], "changes": changes, "citations": citations})


class LatestNoteProvider(OfflineRuleProvider):
    """Fixed latest-authorized-note pass using the same extraction grammar."""
    mode = "latest-note-baseline"
    latest_only = True
