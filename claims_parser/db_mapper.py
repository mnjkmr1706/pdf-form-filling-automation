"""Resolve a FormSchema to DB paths.

Pipeline:
  1. heuristic_resolve - cheap, no-LLM fast-path (token-set Jaccard with type/block bias)
  2. llm_resolve       - batched LLM call (gpt-5-mini, structured output) for residuals
  3. cache             - save/load the merged FieldMap keyed by (schema_fp, db_schema_fp)

The cache is form-keyed and case-independent: the same blank form filled for
N patients runs the LLM exactly once.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openai import OpenAI

from claims_parser.db_loader import (
    _normalize_path,
    canonical_db_paths,
    db_schema_fingerprint,
    flatten_db,
)
from claims_parser.db_mapping_models import (
    FieldMap,
    FieldResolution,
    LLMResolverResponse,
    Resolution,
)
from claims_parser.schema_models import FormField, FormSchema

DEFAULT_MODEL = "gpt-5-mini"

_DATE_PATH_HINTS = (
    "date", "dob", "dateofbirth", "dateofservice",
    "effectivedate", "datefrom", "dateto", "datereceived",
    "submissiondate", "filingdate",
)
_PHONE_PATH_HINTS = ("phone", "fax")
_EMAIL_PATH_HINTS = ("email",)
_BLOCK_HINTS = {
    "patient": ("patient",),
    "subscriber": ("subscriber", "insured", "policyholder"),
    "dependent": ("dependent",),
    "provider": ("provider", "facility", "billing", "rendering", "servicing"),
    "payer": ("payer", "insurer", "insurance", "plan"),
    "claim": ("claim", "appeal", "denial"),
}


# ---------------------------------------------------------------------------
# Heuristic (no-LLM) fast-path
# ---------------------------------------------------------------------------

_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _tokens(s: str) -> set[str]:
    """Split snake_case, camelCase, and mixed forms into a lowercase token set."""
    decamel = _CAMEL_SPLIT.sub(" ", s)
    return set(re.findall(r"[a-z0-9]+", decamel.lower()))


def _block_for_section(section: Optional[str]) -> Optional[str]:
    if not section:
        return None
    sl = section.lower()
    for block, hints in _BLOCK_HINTS.items():
        if any(h in sl for h in hints):
            return block
    return None


def _default_transform_for_type(field: FormField) -> Optional[str]:
    if field.field_type == "date":
        return f"date:{field.format_hint or 'YYYY-MM-DD'}"
    if field.field_type == "phone":
        return "phone:formatted"
    if field.field_type == "currency":
        return "currency"
    if field.field_type == "number":
        return "number"
    return None


def heuristic_resolve(
    field: FormField,
    normalized_db_paths: list[str],
    confidence_floor: float = 0.7,
) -> Optional[FieldResolution]:
    """Return a high-confidence FieldResolution, or None if ambiguous (LLM should decide)."""
    if field.field_type in ("checkbox", "radio_group"):
        return None  # option mapping is too nuanced for token overlap

    f_toks = _tokens(field.field_id) | _tokens(field.label)
    if field.section:
        f_toks |= _tokens(field.section)
    if not f_toks:
        return None

    preferred_block = _block_for_section(field.section)
    best_score = 0.0
    best_path = ""
    for path in normalized_db_paths:
        p_toks = _tokens(path)
        if not p_toks:
            continue
        if field.field_type == "date" and not any(h in path.lower() for h in _DATE_PATH_HINTS):
            continue
        if field.field_type == "phone" and not any(h in path.lower() for h in _PHONE_PATH_HINTS):
            continue
        if field.field_type == "email" and not any(h in path.lower() for h in _EMAIL_PATH_HINTS):
            continue
        jac = len(f_toks & p_toks) / max(1, len(f_toks | p_toks))
        if preferred_block and path.startswith(preferred_block + "."):
            jac += 0.15
        if jac > best_score:
            best_score = jac
            best_path = path

    if best_score < confidence_floor or not best_path:
        return None
    return FieldResolution(
        field_id=field.field_id,
        confidence="high",
        value=Resolution(
            kind="scalar",
            paths=[best_path],
            transform=_default_transform_for_type(field),
            reason=f"heuristic jaccard={best_score:.2f}",
        ),
    )


# ---------------------------------------------------------------------------
# LLM batch resolution for residuals
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """You receive a list of form fields and the FLATTENED list
of available DB paths (dotted notation; list indices are normalized to "[]").

For each form field, produce a FieldResolution. Allowed `value.kind` values:
- "scalar"       : single DB path supplies the value.
- "compose"      : combine multiple paths via a Python str.format template
                   such as "{0} {1} {2}" (first/middle/last name, for example).
- "service_line" : the field belongs to a table row; use one path with the
                   literal "[{i}]" placeholder where the row index goes, and
                   set `service_line_index` to the 0-based row index inferred
                   from the field_id or label.
- "derived"      : the value is a small predicate over the DB (eq, neq,
                   non_empty, empty). Use `derive` with op/path/value and
                   `true_value` / `false_value`.
- "constant"     : literal value (rare; use only when no DB path applies but
                   a stable constant should be stamped).
- "unmapped"     : no DB path plausibly supplies the value.

For radio_group / checkbox fields:
- Set `value.kind` to "constant" with literal=null.
- For each option in the field's `options` list, emit one OptionResolution
  whose `when` is a Resolution (scalar / derived / constant) that determines
  selection. Use `selected_if="equals"` with `equals_value` for derived
  resolutions, or `"truthy"` to select when the resolved value is non-empty.

Rules:
- NEVER invent paths not in the provided list. Use only the normalized paths
  given. For service_line, use "[{i}]" with literal braces around i.
- NEVER reference field labels, section names, or option values from any
  specific form in your reasoning. Work only from what you are given.
- Apply `transform` when the field_type clearly requires coercion:
  "date:<out_format>" (use the field's format_hint as out_format if given;
  default "YYYY-MM-DD"), "phone:formatted", "phone:digits", "number",
  "currency", "upper", "lower", "strip".
- Confidence: "high" for unambiguous matches, "medium" for plausible,
  "low" for guesses, "unmapped" if no plausible path exists.
- Set `reason` to one short sentence explaining the choice.

Return every input field with exactly one FieldResolution, in the same order.
"""


def _load_openai_key() -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not found. Add it to parser.env.")
    return key


def _field_summary(f: FormField) -> dict:
    return {
        "field_id": f.field_id,
        "label": f.label,
        "section": f.section,
        "field_type": f.field_type,
        "options": f.options,
        "format_hint": f.format_hint,
        "required": f.required,
    }


def _sample_values(db: dict, paths: list[str], n: int = 60) -> dict:
    """Provide up to n (normalized_path, sample_value) pairs to disambiguate similar paths."""
    flat = flatten_db(db)
    seen: dict[str, object] = {}
    for k, v in flat.items():
        norm = _normalize_path(k)
        if norm in seen:
            continue
        if v in ("", None, [], {}):
            continue
        seen[norm] = v
        if len(seen) >= n:
            break
    return seen


def llm_resolve(
    fields: list[FormField],
    normalized_db_paths: list[str],
    db_sample: dict,
    model: str = DEFAULT_MODEL,
    client: Optional[OpenAI] = None,
) -> list[FieldResolution]:
    if not fields:
        return []
    if client is None:
        client = OpenAI(api_key=_load_openai_key())
    user_msg = json.dumps(
        {
            "form_fields": [_field_summary(f) for f in fields],
            "db_paths": normalized_db_paths,
            "db_sample_values": db_sample,
        },
        indent=2,
    )
    response = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format=LLMResolverResponse,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(
            f"Model {model} returned no parsed result. "
            f"Finish reason: {response.choices[0].finish_reason}"
        )
    # Build a lookup by field_id so we can preserve input field order even if
    # the model reorders.
    by_id = {fr.field_id: fr for fr in parsed.field_resolutions}
    return [
        by_id.get(
            f.field_id,
            FieldResolution(
                field_id=f.field_id,
                confidence="unmapped",
                value=Resolution(kind="unmapped", reason="missing from LLM response"),
            ),
        )
        for f in fields
    ]


# ---------------------------------------------------------------------------
# Schema fingerprint + cache
# ---------------------------------------------------------------------------

def compute_schema_fingerprint(schema: FormSchema) -> str:
    """Stable hash over field shape (excludes source_text, which is spatial)."""
    body = json.dumps(
        [
            {
                "field_id": f.field_id,
                "label": f.label,
                "section": f.section,
                "field_type": f.field_type,
                "options": sorted(f.options) if f.options else None,
            }
            for f in schema.fields
        ],
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def save_fillmap(fmap: FieldMap, output_path) -> None:
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(fmap.model_dump_json(indent=2))


def load_fillmap(input_path) -> FieldMap:
    return FieldMap.model_validate_json(Path(input_path).read_text())


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

def resolve_form(
    schema: FormSchema,
    db: dict,
    *,
    fillmap_path: Optional[Path] = None,
    pdf_basename: str = "",
    model: str = DEFAULT_MODEL,
    client: Optional[OpenAI] = None,
    refresh: bool = False,
) -> FieldMap:
    """Full resolution: try cache, then heuristic, then LLM. Save back to cache.

    If fillmap_path exists and fingerprints match, return cached. Otherwise
    resolve via heuristic + LLM, write to fillmap_path, and return.
    """
    schema_fp = compute_schema_fingerprint(schema)
    db_fp = db_schema_fingerprint(db)

    if fillmap_path is not None and Path(fillmap_path).exists() and not refresh:
        try:
            cached = load_fillmap(fillmap_path)
            if cached.schema_fingerprint == schema_fp and cached.db_schema_fingerprint == db_fp:
                return cached
        except Exception:
            pass  # corrupt cache: fall through and rebuild

    paths = canonical_db_paths()
    db_sample = _sample_values(db, paths)

    heuristic_hits: dict[str, FieldResolution] = {}
    residuals: list[FormField] = []
    for f in schema.fields:
        hit = heuristic_resolve(f, paths)
        if hit is not None:
            heuristic_hits[f.field_id] = hit
        else:
            residuals.append(f)

    llm_hits = llm_resolve(residuals, paths, db_sample, model=model, client=client)
    llm_by_id = {fr.field_id: fr for fr in llm_hits}

    field_resolutions: list[FieldResolution] = []
    for f in schema.fields:
        if f.field_id in heuristic_hits:
            field_resolutions.append(heuristic_hits[f.field_id])
        else:
            field_resolutions.append(
                llm_by_id.get(
                    f.field_id,
                    FieldResolution(
                        field_id=f.field_id,
                        confidence="unmapped",
                        value=Resolution(kind="unmapped", reason="no resolver produced a result"),
                    ),
                )
            )

    unresolved = [fr.field_id for fr in field_resolutions if fr.confidence == "unmapped"]
    fmap = FieldMap(
        schema_fingerprint=schema_fp,
        db_schema_fingerprint=db_fp,
        form_pdf_basename=pdf_basename,
        created_at=datetime.now(timezone.utc).isoformat(),
        field_resolutions=field_resolutions,
        unresolved_field_ids=unresolved,
        notes=[
            f"heuristic resolved {len(heuristic_hits)}/{len(schema.fields)}",
            f"LLM resolved {len(residuals) - sum(1 for fr in llm_hits if fr.confidence == 'unmapped')}/{len(residuals)}",
        ],
    )
    if fillmap_path is not None:
        save_fillmap(fmap, fillmap_path)
    return fmap
