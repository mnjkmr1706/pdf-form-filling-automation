"""Apply a FieldMap to a DB JSON to produce a FilledFormSchema.

Pure, deterministic, no network. Every behavior is driven by the FieldMap;
no form-specific knowledge lives here. Preserves widget_bindings on each
FormField untouched so the mapped AcroForm writer keeps working.
"""
from __future__ import annotations

from typing import Any, Optional

from claims_parser.db_coercers import apply_transform, compose
from claims_parser.db_loader import db_path_get
from claims_parser.db_mapping_models import (
    FieldMap,
    FieldResolution,
    OptionResolution,
    Resolution,
)
from claims_parser.filler_models import FilledFormField, FilledFormSchema
from claims_parser.schema_models import FormField, FormSchema


def fill_from_db(schema: FormSchema, fmap: FieldMap, db: dict) -> FilledFormSchema:
    by_id = {fr.field_id: fr for fr in fmap.field_resolutions}
    filled: list[FilledFormField] = []
    for f in schema.fields:
        filled.append(_fill_field(f, by_id.get(f.field_id), db))
    return FilledFormSchema.from_schema(schema, filled)


def _fill_field(field: FormField, fr: Optional[FieldResolution], db: dict) -> FilledFormField:
    if fr is None:
        return _empty(field)
    if field.field_type in ("checkbox", "radio_group"):
        return _fill_choice(field, fr, db)
    raw = _resolve_value(fr.value, db)
    val = apply_transform(raw, fr.value.transform) if raw is not None else None
    return FilledFormField(**field.model_dump(), value=val)


def _fill_choice(field: FormField, fr: FieldResolution, db: dict) -> FilledFormField:
    selected: list[str] = []
    for opt in fr.options:
        if _option_matches(opt, db):
            selected.append(opt.option_label)
    if field.field_type == "radio_group":
        value: Any = selected[0] if selected else None
    else:
        value = selected if selected else None
    return FilledFormField(**field.model_dump(), value=value)


def _option_matches(opt: OptionResolution, db: dict) -> bool:
    val = _resolve_value(opt.when, db)
    if opt.selected_if == "always":
        return val is not None and val != ""
    if opt.selected_if == "equals":
        return str(val) == (opt.equals_value or "")
    if val is None or val == "" or val is False:
        return False
    return True


def _resolve_value(r: Resolution, db: dict) -> Any:
    if r.kind == "constant":
        return r.literal
    if r.kind == "unmapped":
        return None
    if r.kind == "scalar":
        return db_path_get(db, r.paths[0]) if r.paths else None
    if r.kind == "compose":
        raws = [db_path_get(db, p) for p in r.paths]
        return compose(raws, r.template or "")
    if r.kind == "service_line":
        idx = r.service_line_index or 0
        if not r.paths:
            return None
        path = r.paths[0].replace("{i}", str(idx))
        return db_path_get(db, path)
    if r.kind == "derived":
        d = r.derive
        if d is None:
            return None
        observed = db_path_get(db, d.path)
        if d.op == "eq":
            matched = str(observed) == str(d.value or "")
        elif d.op == "neq":
            matched = str(observed) != str(d.value or "")
        elif d.op == "non_empty":
            matched = observed not in (None, "")
        elif d.op == "empty":
            matched = observed in (None, "")
        else:
            raise ValueError(f"Unknown derived op: {d.op!r}")
        return d.true_value if matched else d.false_value
    raise ValueError(f"Unknown Resolution.kind: {r.kind!r}")


def _empty(field: FormField) -> FilledFormField:
    return FilledFormField(**field.model_dump(), value=None)
