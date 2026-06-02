"""LLM-driven fill of the canonical DB JSON template.

One call against the fixed DBEnvelope shape. Replaces per-form Agent 2
for dummy-data generation: same fictional patient/claim drives every
form for a given case.

In production, real values arrive in the JSON and this module is skipped.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from openai import OpenAI

from claims_parser.db_template_models import DBEnvelope

DEFAULT_MODEL = "gpt-5-mini"

SYSTEM_PROMPT = """You receive a JSON document representing a healthcare claim
appeal record. Some fields are populated; others are empty strings, empty lists,
or null. Return the SAME document with every empty field filled with realistic,
internally-consistent fictional values.

Rules:
- Do not change any field that already has a non-empty value.
- Keep all field names exactly as given (including any typos in source keys).
- Values must be consistent: patient name, DOB, addresses, IDs all belong to
  the same fictional individual; dates are mutually plausible; amounts add up.
- Date format: keep the format consistent with whatever other dates in the
  document use. If all dates are empty, use ISO YYYY-MM-DD.
- Service lines: if the array has entries already, fill in their empty fields.
  If the array is empty, add 2 to 4 plausible service lines.
- Never use real personal data. All names, addresses, phones, emails, IDs,
  and identifiers must be invented.
- Do not invent NEW top-level keys or sub-objects. Only fill what the schema
  defines. The response must validate against the provided pydantic schema.
"""


def _load_openai_key() -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not found. Add it to parser.env.")
    return key


def fill_db_template(
    template: dict,
    model: str = DEFAULT_MODEL,
    client: Optional[OpenAI] = None,
) -> dict:
    """Take a (possibly sparse) DB JSON dict and return a fully-populated dict.

    The input is validated/normalized through DBEnvelope before sending so
    the LLM sees the exact target shape, even if the caller omitted nested keys.
    """
    if client is None:
        client = OpenAI(api_key=_load_openai_key())
    normalized = DBEnvelope.model_validate(template).model_dump()
    user_msg = json.dumps(normalized, indent=2)
    response = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format=DBEnvelope,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(
            f"Model {model} returned no parsed result. "
            f"Finish reason: {response.choices[0].finish_reason}"
        )
    return parsed.model_dump()


def save_filled_db(data: dict, output_path) -> None:
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def load_db_envelope(input_path) -> dict:
    return json.loads(Path(input_path).read_text())
