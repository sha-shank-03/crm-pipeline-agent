import json


def render(packet: dict) -> str:
    proposal = packet["proposal"]
    action = proposal["action"]
    lines = [f"Proposal: {proposal['id']} ({proposal['status']})", f"Opportunity: {action['opportunity_id']}",
             f"Expected versions: record={action['expected_version']}, evidence={action['expected_evidence_revision']}, account={action['expected_account_revision']}",
             f"As of: {action['as_of']} | policy: {action['policy_version']}", f"Exact action SHA-256: {proposal['digest']}"]
    for change in action["changes"]:
        lines.append(f"{change['field']}: {json.dumps(change['before'], ensure_ascii=False)} -> {json.dumps(change['after'], ensure_ascii=False)}")
    for citation in action["citations"]:
        lines.append(f"Evidence {citation['note_id']} v{citation['note_revision']} / {citation['line_id']}: {citation['quote']}")
    return "\n".join(lines)
