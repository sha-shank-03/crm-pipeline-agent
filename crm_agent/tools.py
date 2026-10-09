from .protocol import ProtocolError
from .provider import ToolCall

SCHEMAS = {
    "list_opportunities": {},
    "get_opportunity": {"id": str, "include_history": bool},
    "list_notes": {"opportunity_id": str},
    "read_note": {"note_id": str},
    "stage_proposal": {"opportunity_id": str, "expected_version": int, "expected_evidence_revision": int,
                       "expected_account_revision": int, "changes": list, "citations": list},
}


def tool_schemas():
    types = {str: "string", int: "integer", bool: "boolean", list: "array"}
    schemas = [{"name": name, "parameters": {"type": "object", "additionalProperties": False,
             "required": list(fields), "properties": {key: {"type": types[value]} for key, value in fields.items()}}}
            for name, fields in SCHEMAS.items()]
    properties = next(item for item in schemas if item["name"] == "stage_proposal")["parameters"]["properties"]
    for key, fields in {"changes": {"field": "string", "before": "string", "after": "string"},
                        "citations": {"field": "string", "note_id": "string", "note_revision": "integer", "line_id": "string", "line_hash": "string", "quote": "string"}}.items():
        properties[key].update(minItems=1, maxItems=2, items={"type": "object", "additionalProperties": False,
            "required": list(fields), "properties": {field: {"type": kind} for field, kind in fields.items()}})
    return schemas


def validate_call(call):
    if not isinstance(call, ToolCall) or call.name not in SCHEMAS or not isinstance(call.arguments, dict):
        raise ProtocolError("Provider selected an unregistered tool")
    fields = SCHEMAS[call.name]
    if call.arguments.keys() != fields.keys() or any(type(call.arguments[key]) is not kind for key, kind in fields.items()):
        raise ProtocolError("Invalid tool argument fields or types")
    return call


class Dispatcher:
    def __init__(self, client, run_id):
        self.client = client
        self.run_id = run_id

    def arguments(self, call):
        validate_call(call)
        arguments = dict(call.arguments)
        if call.name == "stage_proposal":
            arguments["run_id"] = self.run_id
        return arguments

    def execute(self, call, request_id):
        return self.client.call(call.name, self.arguments(call), request_id)
