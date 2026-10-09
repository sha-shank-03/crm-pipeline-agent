import os
from pathlib import Path
import subprocess
import time
import uuid

from .protocol import ProtocolError, encode, strict_json

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = frozenset({"init", "list_opportunities", "get_opportunity", "list_notes", "read_note",
                        "stage_proposal", "review", "approve", "reject", "apply", "receipt", "inspect", "edit_opportunity", "fixture_change", "operation_result"})
PRINCIPALS = frozenset({"proposer-north", "manager-north", "executor-north", "proposer-south", "manager-south", "executor-south", "fixture-operator"})
MUTATIONS = frozenset({"stage_proposal", "approve", "reject", "apply", "edit_opportunity", "fixture_change", "init"})


class DomainError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class OutcomeUnknown(DomainError):
    def __init__(self, request_id: str):
        super().__init__("OUTCOME_UNKNOWN", "Owned Java process timed out; reconcile the original operation key")
        self.request_id = request_id


class JavaClient:
    def __init__(self, store: Path, principal: str, as_of: str = "2026-10-08", *, cancel_file: Path | None = None):
        if principal not in PRINCIPALS:
            raise ValueError("Unknown configured principal")
        self.store = Path(store).resolve()
        self.principal = principal
        self.as_of = as_of
        self.cancel_file = Path(cancel_file) if cancel_file else None

    def call(self, operation: str, arguments: dict, request_id: str | None = None) -> dict:
        try:
            return self._call(operation, arguments, request_id)
        except DomainError as error:
            if operation not in MUTATIONS and error.code in {"STORE_BUSY", "JAVA_TIMEOUT"}:
                return self._call(operation, arguments, request_id)  # at most one read-only transient retry
            raise

    def _call(self, operation: str, arguments: dict, request_id: str | None = None) -> dict:
        if operation not in OPERATIONS:
            raise ProtocolError("Unknown Java operation")
        request_id = request_id or str(uuid.uuid4())
        payload = encode({"schema_version": 1, "request_id": request_id, "operation": operation, "args": arguments})
        if len(payload) > 65536:
            raise ProtocolError("Request exceeds 64 KiB")
        from tools.build import verify_dependency
        try:
            jars = verify_dependency()
        except ValueError as error:
            raise DomainError("BUILD_REQUIRED", str(error)) from error
        classes = ROOT / "build" / "classes"
        if not (classes / "crm" / "Main.class").is_file():
            raise DomainError("BUILD_REQUIRED", "Provision the pinned dependency and run tools/build.py first")
        command = ["java", "-Xmx128m", "-cp", os.pathsep.join([str(classes)] + list(map(str, jars))), "crm.Main",
                   "--store", str(self.store), "--principal", self.principal, "--as-of", self.as_of]
        if self.cancel_file and self.cancel_file.exists():
            raise DomainError("CANCELLED", "Cancelled before owned process launch")
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        except OSError as error:
            raise DomainError("BUILD_REQUIRED", "Cannot start the configured Java runtime") from error
        deadline = time.monotonic() + 5
        first = True
        try:
            while True:
                if (self.cancel_file and self.cancel_file.exists()) or time.monotonic() >= deadline:
                    process.kill()
                    process.communicate(timeout=2)
                    if operation in MUTATIONS:
                        raise OutcomeUnknown(request_id)
                    raise DomainError("CANCELLED" if self.cancel_file and self.cancel_file.exists() else "JAVA_TIMEOUT", "Owned read process stopped")
                try:
                    stdout, stderr = process.communicate(input=payload if first else None, timeout=min(0.1, max(0.001, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    first = False
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)
        if process.returncode != 0:
            raise DomainError("JAVA_FAILURE", f"Java exited {process.returncode}; reconcile any pending write")
        if len(stdout) > 262144 or len(stderr) > 65536:
            raise ProtocolError("Response exceeds 256 KiB")
        try:
            response = strict_json(stdout.decode("utf-8", errors="strict"))
        except UnicodeError as error:
            raise ProtocolError("Non-UTF-8 Java response") from error
        if not isinstance(response, dict) or type(response.get("schema_version")) is not int or response["schema_version"] != 1 or type(response.get("ok")) is not bool:
            raise ProtocolError("Invalid Java response envelope")
        if not response["ok"]:
            error = response.get("error", {})
            if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
                raise ProtocolError("Invalid domain error envelope")
            raise DomainError(error["code"], error["message"])
        if response.get("request_id") != request_id or not isinstance(response.get("result"), dict):
            raise ProtocolError("Mismatched request or missing result")
        return response["result"]
