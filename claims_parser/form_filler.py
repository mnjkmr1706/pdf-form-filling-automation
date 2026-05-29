from __future__ import annotations

import json
import os
from typing import Optional

from openai import OpenAI

from claims_parser.filler_models import FilledFormSchema
from claims_parser.schema_models import FormSchema

DEFAULT_MODEL = "gpt-5-mini"

SYSTEM_PROMPT = """You receive a generic JSON schema describing every fillable input on a form
and must return the same schema with a plausible `value` filled in for each field.

Rules:
- Fill EVERY field with a plausible non-empty value. Even if the form text
  suggests a field only applies under some condition (e.g. "If yes, date:"),
  treat the condition as if it applies and produce a value. This output is
  used for end-to-end validation; no field may be left empty.
- Produce values that are internally consistent across fields (e.g. a person's
  name, address, phone, email, and identifiers should belong to the same
  fictional individual; dates should be mutually plausible).
- Values must be fictional but realistic. Do not use real personal data.
- Respect field_type:
    text / long_text  -> a single non-empty string
    date              -> match format_hint if present, otherwise ISO YYYY-MM-DD
    time              -> match format_hint if present, otherwise HH:MM
    phone / email / address / number / currency / identifier / signature -> a single non-empty string
    checkbox          -> a non-empty list drawn ONLY from this field's options
    radio_group       -> exactly one string drawn ONLY from this field's options
- Respect format_hint when present.
- Never invent options that are not in the field's options list.
- Do not modify field_id, label, section, field_type, options, format_hint,
  required, or source_text. Only fill `value`.
- Do not assume any particular form domain. Work only from what the schema says.
"""


def _load_openai_key() -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not found. Add it to parser.env.")
    return key


def fill_form_schema(
    schema: FormSchema,
    model: str = DEFAULT_MODEL,
    client: Optional[OpenAI] = None,
) -> FilledFormSchema:
    if client is None:
        client = OpenAI(api_key=_load_openai_key())

    schema_dict = schema.model_dump()
    for f in schema_dict.get("fields", []):
        f.pop("widget_bindings", None)
    user_msg = json.dumps(schema_dict, indent=2)

    response = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format=FilledFormSchema,
    )

    filled = response.choices[0].message.parsed
    if filled is None:
        raise RuntimeError(
            f"Model {model} returned no parsed result. "
            f"Finish reason: {response.choices[0].finish_reason}"
        )

    bindings_by_id = {f.field_id: f.widget_bindings for f in schema.fields}
    for ff in filled.fields:
        if bindings_by_id.get(ff.field_id) is not None:
            ff.widget_bindings = bindings_by_id[ff.field_id]
    return filled


def save_filled_schema(filled: FilledFormSchema, output_path) -> None:
    from pathlib import Path

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(filled.model_dump_json(indent=2))


def load_filled_schema(input_path) -> FilledFormSchema:
    from pathlib import Path

    return FilledFormSchema.model_validate_json(Path(input_path).read_text())
