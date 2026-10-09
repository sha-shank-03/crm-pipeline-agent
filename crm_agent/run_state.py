import os
from pathlib import Path
import tempfile
import time

from .protocol import encode, strict_json

_WINDOWS = os.name == "nt"
_REPLACE_DELAYS = (0.02, 0.05, 0.1)


def _replace(temporary, path):
    # A Windows access denial may be permanent. Give only these native denial
    # codes a bounded opportunity to clear; never remove/truncate the target.
    first_error = None
    for delay in (*_REPLACE_DELAYS, None):
        try:
            os.replace(temporary, path)
            return
        except OSError as error:
            if not _WINDOWS or getattr(error, "winerror", None) not in {5, 32, 33}:
                raise
            if first_error is None:
                first_error = error
            if delay is None:
                first_error.add_note("Atomic checkpoint replacement exhausted four attempts; original target was not deleted or truncated.")
                raise first_error
            time.sleep(delay)


def save(path: Path, value: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".run-", suffix=".tmp", dir=path.parent)
    primary_error = None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encode(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        _replace(temporary, path)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        except OSError as cleanup_error:
            if primary_error is None:
                raise
            primary_error.add_note("Could not clean uncommitted checkpoint temporary file: " + str(cleanup_error))


def load(path: Path) -> dict:
    if Path(path).stat().st_size > 1024 * 1024:
        raise ValueError("Run checkpoint exceeds 1 MiB")
    value = strict_json(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported run record")
    return value
