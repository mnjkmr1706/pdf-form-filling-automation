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


def db_schema_fingerprint(data: Any) -> str:
    """Hash the set of leaf paths (ignoring list indices and values)."""
    flat = flatten_db(data)
    normed = {_normalize_path(k) for k in flat.keys()}
    body = "\n".join(sorted(normed))
    return hashlib.sha256(body.encode()).hexdigest()[:16]
