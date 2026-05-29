"""Pydantic models for the structured form schema produced by Agent 1.

These models intentionally avoid any domain-specific assumptions. They describe
*what an input on a form looks like* in fully generic terms, so the same schema
shape can describe a healthcare claims form, a tax return, an HR onboarding
form, or anything else with fillable inputs.

Nothing here references specific issuers, section headings, field labels, or
option values. All of those come verbatim from whatever form is being processed.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from claims_parser.mapping_models import WidgetBinding


# Generic semantic input categories — like HTML <input type="..."> values.
# These are NOT form-specific names. The LLM picks the most specific applicable
# type; anything that doesn't fit a more specific bucket falls back to "text"
# or "long_text".
FieldType = Literal[
    "text",        # free-form short text
    "long_text",   # multi-line / textarea
    "date",
    "time",
    "phone",
    "email",
    "address",
    "number",
    "currency",
    "identifier",  # any ID/code (account #, claim #, SSN, NPI, license #, etc.)
    "signature",
    "checkbox",    # zero-or-more selectable options
    "radio_group", # exactly-one selectable option
]


class FormField(BaseModel):
    field_id: str = Field(
        description=(
            "A stable, lowercase snake_case identifier for this field, unique within the form. "
            "Derive it from the label content. Do not invent identifiers that aren't grounded "
            "in the form text."
        )
    )
    label: str = Field(
        description=(
            "The verbatim label as printed on the form, with trailing punctuation like ':' removed. "
            "Do not paraphrase."
        )
    )
    section: Optional[str] = Field(
        default=None,
        description=(
            "The section heading this field belongs to, taken verbatim from the form. "
            "Null if the form has no sections or this field is outside any section."
        ),
    )
    field_type: FieldType = Field(
        description="The semantic type of the input. Pick the most specific applicable category."
    )
    options: Optional[list[str]] = Field(
        default=None,
        description=(
            "For choice-based fields (checkbox or radio_group) ONLY, the exact options as printed. "
            "Null for every other field_type. Do not invent options not visible on the form."
        ),
    )
    format_hint: Optional[str] = Field(
        default=None,
        description=(
            "Any visible format constraint inferred from the form itself (e.g. a date placeholder, "
            "a code pattern shown next to the field). Null if the form does not hint at a format."
        ),
    )
    required: bool = Field(
        description=(
            "True only if the form explicitly marks this field as required. "
            "Do not infer requiredness from field type or convention."
        )
    )
    source_text: str = Field(
        description=(
            "A short verbatim quote from the OCR text that uniquely identifies this field. "
            "Used downstream to map this field back to its position on the page. "
            "Must appear in the input text exactly."
        )
    )
    widget_bindings: Optional[list[WidgetBinding]] = Field(
        default=None,
        description=(
            "Populated only by the mapped AcroForm branch. For radio_group fields, "
            "one binding per option-widget. For all other field types: one binding. "
            "Null for non-editable forms and the legacy AcroForm branch."
        ),
    )


class FormSchema(BaseModel):
    form_title: Optional[str] = Field(
        default=None,
        description="The form title exactly as printed. Null if no title is identifiable.",
    )
    issuer: Optional[str] = Field(
        default=None,
        description=(
            "The organization that issued the form, if identifiable from the form text. "
            "Null if not stated or unclear."
        ),
    )
    sections: list[str] = Field(
        description=(
            "Distinct section headings found on the form, in reading order. "
            "Empty list if the form has no sections."
        )
    )
    fields: list[FormField] = Field(
        description="Every fillable input detected on the form, in reading order."
    )
