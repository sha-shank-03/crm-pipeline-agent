"""Trusted CLI review control; durable key before any approve/reject subprocess."""
from pathlib import Path
import os
import uuid

from .protocol import ProtocolError, encode
from .run_state import load

def review_action(manager, operation, proposal_id, digest, record_path: Path):
    if operation not in {"approve", "reject"}:
        raise ProtocolError("Unsupported review action")
    arguments = {"proposal_id": proposal_id, "digest": digest}
    record_path = Path(record_path)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"schema_version": 1, "operation": operation, "request_id": str(uuid.uuid4()),
              "arguments": arguments, "principal": manager.principal, "as_of": manager.as_of}
    try:
        # Exclusive immutable creation: another operator cannot replace an in-flight key.
        with record_path.open("xb") as stream:
            stream.write(encode(record) + b"\n"); stream.flush(); os.fsync(stream.fileno())
        existed = False
    except FileExistsError:
        existed = True
    if existed:
        record = load(record_path)
        if record.keys() != {"schema_version", "operation", "request_id", "arguments", "principal", "as_of"} or record["operation"] != operation or record["arguments"] != arguments or record["principal"] != manager.principal or record["as_of"] != manager.as_of or not isinstance(record["request_id"], str) or not record["request_id"]:
            raise ProtocolError("Review checkpoint changed; operator reconciliation required")
        prior = manager.call("operation_result", {"operation": operation, "request_id": record["request_id"], "args": arguments})
        if prior["found"]:
            return prior["result"]
    return manager.call(operation, arguments, record["request_id"])
