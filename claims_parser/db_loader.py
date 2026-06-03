"""Load the source-of-truth DB JSON, strip the envelope, flatten to dotted paths.

The DB schema is fixed (see db_template_models.DBEnvelope); the only
variation across cases is values and the cardinality of claim.serviceLines.
The fingerprint normalizes list indices so two cases with different
serviceLines counts still share the same form-mapping fingerprint.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def load_db_json(path: str | Path) -> dict[str, Any]:
    """Read the file and return the inner `result` object (envelope stripped)."""
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or "result" not in raw:
        raise ValueError(f"{path}: expected an object with a top-level 'result' key")
    result = raw["result"]
    if not isinstance(result, dict):
        raise ValueError(f"{path}: 'result' must be an object")
    return result


def flatten_db(data: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts/lists into dotted paths.

    Lists use bracket notation: 'claim.serviceLines[0].procedureCode'.
    Leaves are anything that isn't dict or list. Empty strings are preserved.
    """
    out: dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else k
            out.update(flatten_db(v, key))
    elif isinstance(data, list):
        if not data:
            out[prefix] = []
        else:
            for i, item in enumerate(data):
                key = f"{prefix}[{i}]"
                out.update(flatten_db(item, key))
    else:
        out[prefix] = data
    return out


def db_path_get(data: dict, dotted: str) -> Any:
    """Look up a dotted path with [N] list indexing. Returns None if any segment is missing."""
    parts: list[str | int] = []
    buf = ""
    i = 0
    while i < len(dotted):
        ch = dotted[i]
        if ch == ".":
            if buf:
                parts.append(buf)
                buf = ""
            i += 1
        elif ch == "[":
            if buf:
                parts.append(buf)
                buf = ""
            j = dotted.index("]", i)
            parts.append(int(dotted[i + 1:j]))
            i = j + 1
        else:
            buf += ch
            i += 1
    if buf:
        parts.append(buf)

    cur: Any = data
    for p in parts:
        if cur is None:
            return None
        if isinstance(p, int):
            if not isinstance(cur, list) or p >= len(cur):
                return None
            cur = cur[p]
        else:
            if not isinstance(cur, dict) or p not in cur:
                return None
            cur = cur[p]
    return cur


def _normalize_path(path: str) -> str:
    out, i = "", 0
    while i < len(path):
        if path[i] == "[":
            j = path.index("]", i)
            out += "[]"
            i = j + 1
        else:
            out += path[i]
            i += 1
    return out


def canonical_db_paths() -> list[str]:
    """Return the canonical normalized DB paths derived from DBEnvelope.

    Unlike flattening an instance, this is stable across cases — it doesn't
    depend on whether lists are populated or empty. Use this as the path
    set for both the fingerprint and the LLM resolver prompt.
    """
    from claims_parser.db_template_models import DBEnvelope

    def _walk(model_cls, prefix: str, out: list[str]) -> None:
        import typing as _t
        try:
            fields = model_cls.model_fields
        except AttributeError:
            out.append(prefix)
            return
        for name, info in fields.items():
            key = f"{prefix}.{name}" if prefix else name
            ann = info.annotation
            origin = _t.get_origin(ann)
            args = _t.get_args(ann)
            if origin is list:
                inner = args[0] if args else None
                if inner is not None and hasattr(inner, "model_fields"):
                    _walk(inner, key + "[]", out)
                else:
                    out.append(key + "[]")
            elif hasattr(ann, "model_fields"):
                _walk(ann, key, out)
            else:
                out.append(key)

    paths: list[str] = []
    _walk(DBEnvelope, "", paths)
    # Strip "result." prefix and the envelope-level keys; the rest of the
    # codebase treats the envelope-stripped `result` as the root.
    out: list[str] = []
    for p in paths:
        if p.startswith("result."):
            out.append(p[len("result."):])
        elif "." not in p:
            continue  # drop envelope-level scalars (success, message, executionTimeSec)
    return sorted(set(out))


def db_schema_fingerprint(data: Any = None) -> str:
    """Hash the canonical key set of the DB schema.

    The `data` argument is accepted but ignored: the schema fingerprint is
    derived from DBEnvelope so it stays stable across cases regardless of
    list cardinality.
    """
    body = "\n".join(canonical_db_paths())
    return hashlib.sha256(body.encode()).hexdigest()[:16]
