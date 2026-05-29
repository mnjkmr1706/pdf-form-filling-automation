"""AcroForm-to-FormSchema adapter (Agent 1 equivalent for editable forms).

Enumerates widgets directly from the PDF, infers labels (Tiers 1-3), groups
radio buttons into radio_group fields, optionally refines coarse 'text' types
into specific FieldType categories with one LLM call, and returns:

    (FormSchema, AcroFormBinding)

The FormSchema is shape-compatible with the schema produced by the
non-editable Agent 1, so fill_schema.py works unchanged.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

import pymupdf
from openai import OpenAI
from pydantic import BaseModel

from claims_parser.acroform_label_inference import (
    WordTuple,
    infer_label_for_rect,
    infer_label_for_widget,
    vision_label_residuals,
)
from claims_parser.acroform_models import (
    AcroFormBinding,
    AcroFormFieldBinding,
    OptionBinding,
)
from claims_parser.schema_models import FieldType, FormField, FormSchema


DEFAULT_REFINE_MODEL = "gpt-5-mini"
LONG_TEXT_MIN_HEIGHT_PT = 30.0


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _snake(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", label.strip().lower()).strip("_")
    return s or "field"


def _uniqify(field_id: str, taken: set[str]) -> str:
    if field_id not in taken:
        taken.add(field_id)
        return field_id
    i = 2
    while f"{field_id}_{i}" in taken:
        i += 1
    out = f"{field_id}_{i}"
    taken.add(out)
    return out


def _category_for_widget(widget) -> str:
    """Returns 'text', 'checkbox', 'radiobutton', 'choice', 'signature'."""
    t = (widget.field_type_string or "").lower()
    if t in ("checkbox",):
        return "checkbox"
    if t in ("radiobutton",):
        return "radiobutton"
    if t in ("listbox", "combobox"):
        return "choice"
    if t in ("signature",):
        return "signature"
    return "text"


def _on_value_for_button(widget) -> str:
    try:
        s = widget.on_state()
    except Exception:
        s = None
    return s or "Yes"


def _bounding_rect(rects: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[2] for r in rects)
    y1 = max(r[3] for r in rects)
    return x0, y0, x1, y1


# -----------------------------------------------------------------------------
# LLM type refinement
# -----------------------------------------------------------------------------

class _RefinementEntry(BaseModel):
    field_id: str
    field_type: FieldType
    format_hint: Optional[str] = None


class _Refinements(BaseModel):
    items: list[_RefinementEntry]


REFINE_SYSTEM_PROMPT = """You receive a list of input fields extracted from an editable form. For each
entry the source widget category is already known. Refine the field_type to
the most specific applicable value from this set:
  text, long_text, date, time, phone, email, address, number, currency,
  identifier, signature, checkbox, radio_group.

Rules:
- If widget_category is 'checkbox', field_type MUST be 'checkbox'.
- If widget_category is 'radio_group' or 'choice', field_type MUST be 'radio_group'.
- If widget_category is 'signature', field_type MUST be 'signature'.
- If widget_category is 'text', pick the most specific applicable type
  based on the label (e.g. label 'Date of Service' -> 'date'; label
  'Provider NPI' -> 'identifier'; multi-line address box -> 'address';
  free-form reason / narrative box -> 'long_text').
- format_hint: only when an explicit pattern is visible in the label (e.g.
  'MM/DD/YYYY'). Otherwise null.
- Return exactly one entry per input field, keeping field_id unchanged.
- Do NOT add, remove, or rename fields.
"""


def _load_openai_key() -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not found. Add it to parser.env.")
    return key


def _refine_with_llm(
    drafts: list[FormField],
    widget_categories: dict[str, str],
    model: str,
    client: Optional[OpenAI],
) -> list[FormField]:
    if not drafts:
        return drafts
    if client is None:
        client = OpenAI(api_key=_load_openai_key())

    payload = [
        {
            "field_id": f.field_id,
            "label": f.label,
            "widget_category": widget_categories[f.field_id],
            "options": f.options,
        }
        for f in drafts
    ]
    response = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": REFINE_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
        response_format=_Refinements,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        return drafts
    by_id = {it.field_id: it for it in parsed.items}
    refined: list[FormField] = []
    for f in drafts:
        r = by_id.get(f.field_id)
        if r is None:
            refined.append(f)
            continue
        refined.append(FormField(
            field_id=f.field_id,
            label=f.label,
            section=f.section,
            field_type=r.field_type,
            options=f.options,
            format_hint=r.format_hint,
            required=f.required,
            source_text=f.source_text,
        ))
    return refined


# -----------------------------------------------------------------------------
# Main extraction
# -----------------------------------------------------------------------------

def extract_acroform(
    pdf_path: str | Path,
    refine_types: bool = True,
    use_vision_residuals: bool = True,
    refine_model: str = DEFAULT_REFINE_MODEL,
    client: Optional[OpenAI] = None,
) -> tuple[FormSchema, AcroFormBinding]:
    pdf_path = Path(pdf_path)
    doc = pymupdf.open(pdf_path)
    try:
        # ------------------------------------------------------------------
        # Pass 1: collect widgets + Tier 1/2 labels per page
        # ------------------------------------------------------------------
        # records[i] = dict(widget, page, category, label, label_source, rect)
        records: list[dict] = []
        words_by_page: dict[int, list[WordTuple]] = {}

        for page_num, page in enumerate(doc, start=1):
            words = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]
            words_by_page[page_num] = words
            for widget in page.widgets() or []:
                category = _category_for_widget(widget)
                label, source = infer_label_for_widget(widget, words)
                r = widget.rect
                records.append({
                    "widget": widget,
                    "page": page_num,
                    "category": category,
                    "label": label,
                    "label_source": source,
                    "rect": (r.x0, r.y0, r.x1, r.y1),
                    "xref": widget.xref,
                    "field_name": widget.field_name or "",
                })

        # ------------------------------------------------------------------
        # Pass 2 (optional): vision residuals for widgets without labels
        # ------------------------------------------------------------------
        if use_vision_residuals:
            unresolved: dict[int, list[tuple[int, tuple[float, float, float, float]]]] = {}
            marker_to_record: dict[int, int] = {}
            marker_id = 0
            for idx, rec in enumerate(records):
                if rec["label"]:
                    continue
                marker_id += 1
                unresolved.setdefault(rec["page"], []).append((marker_id, rec["rect"]))
                marker_to_record[marker_id] = idx

            if unresolved:
                resolved = vision_label_residuals(
                    pdf_path, unresolved, model=refine_model, client=client,
                )
                for mid, label in resolved.items():
                    rec_idx = marker_to_record.get(mid)
                    if rec_idx is not None and label:
                        records[rec_idx]["label"] = label
                        records[rec_idx]["label_source"] = "vision"

        # ------------------------------------------------------------------
        # Pass 3: group radio buttons by parent field_name into radio_groups
        # ------------------------------------------------------------------
        radio_groups: dict[str, list[dict]] = {}
        non_radio: list[dict] = []
        for rec in records:
            if rec["category"] == "radiobutton":
                radio_groups.setdefault(rec["field_name"], []).append(rec)
            else:
                non_radio.append(rec)

        # ------------------------------------------------------------------
        # Pass 4: build draft FormFields + bindings
        # ------------------------------------------------------------------
        taken: set[str] = set()
        draft_fields: list[FormField] = []
        bindings: list[AcroFormFieldBinding] = []
        widget_category_by_id: dict[str, str] = {}

        for rec in non_radio:
            label = rec["label"] or rec["field_name"] or "field"
            field_id = _uniqify(_snake(label), taken)
            cat = rec["category"]
            widget = rec["widget"]
            options: Optional[list[str]] = None
            on_value: Optional[str] = None

            if cat == "checkbox":
                ftype: FieldType = "checkbox"
                options = [label]
                on_value = _on_value_for_button(widget)
                bind_category = "checkbox"
            elif cat == "choice":
                ftype = "radio_group"
                cv = list(getattr(widget, "choice_values", None) or [])
                options = cv if cv else None
                bind_category = "choice"
            elif cat == "signature":
                ftype = "signature"
                bind_category = "signature"
            else:  # text
                ftype = "long_text" if (rec["rect"][3] - rec["rect"][1]) > LONG_TEXT_MIN_HEIGHT_PT else "text"
                bind_category = "text"

            draft_fields.append(FormField(
                field_id=field_id,
                label=label,
                section=None,
                field_type=ftype,
                options=options,
                format_hint=None,
                required=False,
                source_text=label,
            ))
            bindings.append(AcroFormFieldBinding(
                field_id=field_id,
                widget_category=bind_category,
                page=rec["page"],
                widget_xref=rec["xref"],
                widget_name=rec["field_name"] or None,
                rect=list(rec["rect"]),
                on_value=on_value,
            ))
            widget_category_by_id[field_id] = bind_category

        # Radio groups: one FormField per group
        for parent_name, members in radio_groups.items():
            rects = [m["rect"] for m in members]
            group_rect = _bounding_rect(rects)
            words = words_by_page.get(members[0]["page"], [])
            group_label = (
                infer_label_for_rect(group_rect, words, assume_button=False)
                or parent_name
                or "choice"
            )
            field_id = _uniqify(_snake(group_label), taken)

            option_labels: list[str] = []
            option_bindings: list[OptionBinding] = []
            for m in members:
                opt_label = m["label"] or _on_value_for_button(m["widget"])
                option_labels.append(opt_label)
                option_bindings.append(OptionBinding(
                    label=opt_label,
                    widget_xref=m["xref"],
                    widget_name=m["field_name"],
                    on_value=_on_value_for_button(m["widget"]),
                    page=m["page"],
                ))

            draft_fields.append(FormField(
                field_id=field_id,
                label=group_label,
                section=None,
                field_type="radio_group",
                options=option_labels,
                format_hint=None,
                required=False,
                source_text=group_label,
            ))
            bindings.append(AcroFormFieldBinding(
                field_id=field_id,
                widget_category="radio_group",
                page=members[0]["page"],
                options=option_bindings,
            ))
            widget_category_by_id[field_id] = "radio_group"

        # ------------------------------------------------------------------
        # Pass 5 (optional): LLM type refinement
        # ------------------------------------------------------------------
        if refine_types and draft_fields:
            draft_fields = _refine_with_llm(
                draft_fields, widget_category_by_id, refine_model, client,
            )

        schema = FormSchema(
            form_title=None,
            issuer=None,
            sections=[],
            fields=draft_fields,
        )
        binding = AcroFormBinding(file_name=pdf_path.name, fields=bindings)
        return schema, binding
    finally:
        doc.close()


def save_binding(binding: AcroFormBinding, output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(binding.model_dump_json(indent=2))


def load_binding(input_path: str | Path) -> AcroFormBinding:
    return AcroFormBinding.model_validate_json(Path(input_path).read_text())
