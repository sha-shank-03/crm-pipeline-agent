"""An authored observation-driven response script for the M1 fictional journey."""
import re

from .provider import ToolCall, Terminal


class ScriptedProvider:
    mode = "scripted"
    model_id = None

    def next(self, messages, tools, limits):
        observed = {message["tool"]: message["result"] for message in messages if message.get("role") == "tool" and "result" in message}
        if "list_opportunities" not in observed:
            return ToolCall("list_opportunities", {})
        opportunities = observed["list_opportunities"]["opportunities"]
        if len(opportunities) != 1 or opportunities[0]["id"] != "opp-101":
            return Terminal("HANDOFF", "M1 authored script requires its single fictional opp-101 fixture")
        if "get_opportunity" not in observed:
            return ToolCall("get_opportunity", {"id": "opp-101", "include_history": True})
        if "list_notes" not in observed:
            return ToolCall("list_notes", {"opportunity_id": "opp-101"})
        if "read_note" not in observed:
            notes = observed["list_notes"]["notes"]
            if len(notes) != 1:
                return Terminal("HANDOFF", "Authored script requires one linked note")
            return ToolCall("read_note", {"note_id": notes[0]["id"]})
        opportunity = observed["get_opportunity"]
        note = observed["read_note"]
        lines = {line["id"]: line for line in note["lines"]}
        if "close" not in lines or "next" not in lines:
            return Terminal("HANDOFF", "Authored evidence lines are missing")
        match = re.fullmatch(r"Confirmed target close is (\d{4}-\d{2}-\d{2})\.", lines["close"]["text"])
        if not match:
            return Terminal("HANDOFF", "Close commitment is outside the authored script grammar")
        replacements = {"close_date": match.group(1), "next_step": lines["next"]["text"]}
        changes, citations = [], []
        for field, line_id in [("close_date", "close"), ("next_step", "next")]:
            if opportunity[field] == replacements[field]:
                continue
            line = lines[line_id]
            changes.append({"field": field, "before": opportunity[field], "after": replacements[field]})
            citations.append({"field": field, "note_id": note["id"], "note_revision": note["revision"],
                              "line_id": line_id, "line_hash": line["line_hash"], "quote": line["text"]})
        if not changes:
            return Terminal("NO_CHANGE", "Both fields already match the authored commitments")
        return ToolCall("stage_proposal", {"opportunity_id": "opp-101", "expected_version": opportunity["version"],
                                         "expected_evidence_revision": opportunity["evidence_revision"],
                                         "expected_account_revision": opportunity["account_revision"],
                                         "changes": changes, "citations": citations})
