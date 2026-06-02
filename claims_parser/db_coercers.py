"""Pure value coercers used by db_filler.

No I/O, no LLM, no globals. Every coercion routes through apply_transform
so the FieldMap.transform string is the only knob the resolver needs.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

_DATE_PATTERNS: list[tuple[str, str]] = [
    ("%Y%m%d",   r"^\d{8}$"),
    ("%m/%d/%Y", r"^\d{1,2}/\d{1,2}/\d{4}$"),
    ("%m-%d-%Y", r"^\d{1,2}-\d{1,2}-\d{4}$"),
    ("%Y-%m-%d", r"^\d{4}-\d{1,2}-\d{1,2}$"),
]

_DATE_OUT_FORMATS = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "MM/DD/YYYY": "%m/%d/%Y",
    "MM-DD-YYYY": "%m-%d-%Y",
    "YYYYMMDD":   "%Y%m%d",
    "DD/MM/YYYY": "%d/%m/%Y",
}


def coerce_date(raw: str, out_format: Optional[str] = None) -> Optional[str]:
    """Parse `raw` and re-emit in `out_format` (default ISO YYYY-MM-DD)."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    parsed: Optional[datetime] = None
    for fmt, pat in _DATE_PATTERNS:
        if re.match(pat, s):
            try:
                parsed = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    out_fmt = _DATE_OUT_FORMATS.get(out_format or "YYYY-MM-DD", "%Y-%m-%d")
    return parsed.strftime(out_fmt)


def coerce_phone(raw: str, style: str = "formatted") -> Optional[str]:
    """style='digits' -> '9093923823'; 'formatted' -> '(909) 392-3823'."""
    if not raw or not isinstance(raw, str):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7:
        return None
    if style == "digits":
        return digits
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return digits


def coerce_number(raw: str) -> Optional[str]:
    if raw is None or raw == "":
        return None
    s = str(raw).replace(",", "").strip()
    try:
        f = float(s)
    except ValueError:
        return None
    if f == int(f):
        return str(int(f))
    return f"{f}"


def coerce_currency(raw: str) -> Optional[str]:
    if raw is None or raw == "":
        return None
    s = str(raw).replace("$", "").replace(",", "").strip()
    try:
        f = float(s)
    except ValueError:
        return None
    return f"{f:,.2f}"


def apply_transform(raw, transform: Optional[str]) -> Optional[str]:
    if raw is None or raw == "":
        return None
    if transform is None:
        return str(raw)
    if transform.startswith("date:"):
        out_fmt = transform.split(":", 1)[1] or None
        return coerce_date(str(raw), out_format=out_fmt)
    if transform == "phone:digits":
        return coerce_phone(str(raw), style="digits")
    if transform in ("phone", "phone:formatted"):
        return coerce_phone(str(raw), style="formatted")
    if transform == "number":
        return coerce_number(str(raw))
    if transform == "currency":
        return coerce_currency(str(raw))
    if transform == "upper":
        return str(raw).upper()
    if transform == "lower":
        return str(raw).lower()
    if transform == "strip":
        return str(raw).strip()
    raise ValueError(f"Unknown transform: {transform!r}")


def compose(values: list, template: str) -> Optional[str]:
    """str.format template, skipping None/empty values cleanly."""
    stringified = ["" if v is None else str(v) for v in values]
    rendered = template.format(*stringified)
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return rendered if rendered else None
