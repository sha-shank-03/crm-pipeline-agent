import json


class ProtocolError(ValueError):
    pass


def strict_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProtocolError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ProtocolError("Non-finite JSON number")

    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        def valid(value, depth=0):
            if depth > 12:
                raise ProtocolError("JSON nesting exceeds limit")
            if isinstance(value, str):
                value.encode("utf-8", errors="strict")
            elif isinstance(value, dict):
                for key, item in value.items():
                    valid(key, depth + 1); valid(item, depth + 1)
            elif isinstance(value, list):
                for item in value: valid(item, depth + 1)
        valid(result)
        return result
    except (json.JSONDecodeError, RecursionError, UnicodeError) as error:
        raise ProtocolError("Invalid JSON") from error


def encode(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as error:
        raise ProtocolError("Value cannot be encoded as strict UTF-8 JSON") from error
