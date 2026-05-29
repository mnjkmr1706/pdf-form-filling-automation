"""Agent 1 — Schema Builder.

Takes a `DocumentContext` (from Azure extraction) and uses an LLM to identify
every fillable input on the form. Produces a `FormSchema`.

This module is deliberately domain-agnostic. No form-specific labels, sections,
issuers, or option values appear anywhere here. All such content is derived
from whatever document is passed in.
"""
from __future__ import annotations

import os
from typing import Optional

from openai import OpenAI

from claims_parser.models import DocumentContext
from claims_parser.schema_models import FormSchema

DEFAULT_MODEL = "gpt-5-mini"


SYSTEM_PROMPT = """You analyze the OCR-extracted contents of a printed form and produce a
structured JSON description of every input the user must fill.

CRITICAL: Forms vary widely in domain, layout, vocabulary, and field set. Do NOT
assume any particular section, label, field, or option exists. Read ONLY what
the input contains and describe what you actually see. The same logical concept
may be a text input on one form and a checkbox on another — do not infer types
from prior knowledge of similar forms.

Your output must list every fillable input on the form. For each input:

- field_id: a stable lowercase snake_case identifier derived from its label.
  Must be unique within the form.
- label: the verbatim label as printed (trim trailing punctuation like ':').
- section: the heading of the section it belongs to, taken verbatim from the
  form. Null if the form has no sections, or if this field sits outside any
  section.
- field_type: the most specific applicable semantic type from the allowed list.
- options: for choice-based fields (checkbox / radio_group) only, the exact
  options as printed. Null for everything else. Do not invent options.
- format_hint: a format constraint only if visible on the form (e.g. a date
  placeholder, a code pattern). Null otherwise.
- required: true only if the form explicitly marks the field as required.
- source_text: a short verbatim quote from the OCR text that uniquely
  identifies this field. Must appear in the input exactly.

Rules:
1. EXCLUDE narrative, instructional, or legal text (eligibility paragraphs,
   disclaimers, page headers/footers, descriptions of when to use the form,
   carrier addresses).
2. INCLUDE every actual input slot, even trivial-looking ones.
3. Never invent fields, sections, options, or formats that are not in the input.
4. Never collapse multiple distinct inputs into one entry.
5. If a single label has nested sub-questions (e.g. a parent question with its
   own Yes/No sub-items), emit one field per sub-question.
6. Process the entire input, across all pages.
"""


def _build_user_message(doc_ctx: DocumentContext) -> str:
    parts: list[str] = []

    parts.append("=== OCR-extracted text of the form (reading order, across pages) ===")
    parts.append(doc_ctx.full_text)

    if doc_ctx.key_value_pairs:
        parts.append("")
        parts.append("=== Layout-detected key-value candidates ===")
        parts.append(
            "(These are labels the layout model flagged as likely form keys. "
            "Most values are empty because the form is blank. Use these as "
            "hints — not as the definitive field set.)"
        )
        for i, kvp in enumerate(doc_ctx.key_value_pairs):
            value_display = repr(kvp.value_text) if kvp.value_text else "(empty)"
            parts.append(f"  [{i}] key={kvp.key_text!r}  value={value_display}")

    if doc_ctx.tables:
        parts.append("")
        parts.append(f"=== Layout-detected tables ({len(doc_ctx.tables)}) ===")
        for ti, tbl in enumerate(doc_ctx.tables):
            parts.append(
                f"Table {ti} on page {tbl.page}: "
                f"{tbl.row_count} rows × {tbl.column_count} cols"
            )
            for c in tbl.cells:
                if c.text.strip():
                    parts.append(f"  [{c.row},{c.column}] {c.text!r}")

    return "\n".join(parts)


def _load_openai_key() -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY not found. Add it to parser.env."
        )
    return key


def build_form_schema(
    doc_ctx: DocumentContext,
    model: str = DEFAULT_MODEL,
    client: Optional[OpenAI] = None,
) -> FormSchema:
    """Call the LLM to produce a structured FormSchema from a DocumentContext.

    Args:
        doc_ctx: The Azure extraction output. Treated as the only source of
            truth — nothing in this function injects domain knowledge.
        model: OpenAI model name. Defaults to gpt-5-mini.
        client: Optional pre-built OpenAI client (useful for testing / reuse).

    Returns:
        A validated FormSchema instance.
    """
    if client is None:
        client = OpenAI(api_key=_load_openai_key())

    user_msg = _build_user_message(doc_ctx)

    response = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format=FormSchema,
    )

    schema = response.choices[0].message.parsed
    if schema is None:
        raise RuntimeError(
            f"Model {model} returned no parsed schema. "
            f"Finish reason: {response.choices[0].finish_reason}"
        )
    return schema


def save_schema(schema: FormSchema, output_path) -> None:
    from pathlib import Path

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(schema.model_dump_json(indent=2))


def load_schema(input_path) -> FormSchema:
    from pathlib import Path

    return FormSchema.model_validate_json(Path(input_path).read_text())
