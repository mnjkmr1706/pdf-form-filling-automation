"""Pydantic models for the schema-to-DB resolution artifact.

A Resolution describes how to produce a value for one FormField from the DB
JSON. The shape is intentionally narrow: only the patterns the canonical
DB schema demands are admitted. New requirements should add a `kind` rather
than smuggling behavior into transform strings.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

ResolutionKind = Literal["scalar", "compose", "service_line", "derived", "constant", "unmapped"]


class DerivedSpec(BaseModel):
    """Tiny predicate spec for kind='derived'."""
    op: Literal["eq", "neq", "non_empty", "empty"]
    path: str = ""
    value: Optional[str] = None
    true_value: Optional[str] = None
    false_value: Optional[str] = None


class Resolution(BaseModel):
    """How to produce a value for one FormField from the DB JSON."""

    kind: ResolutionKind
    paths: list[str] = Field(
        default_factory=list,
        description=(
            "Dotted DB paths into result.*. Empty for kind=constant or unmapped. "
            "Multiple paths only for kind=compose. For kind=service_line, the "
            "single path uses '[{i}]' placeholder."
        ),
    )
    template: Optional[str] = Field(
        default=None,
        description=(
            "Python str.format template for kind=compose. Positional placeholders "
            "{0}, {1}, ... reference `paths` in order. None for other kinds."
        ),
    )
    transform: Optional[str] = Field(
        default=None,
        description=(
            "Coercion spec applied to the raw value(s). Allowed values: "
            "'date:YYYY-MM-DD', 'date:MM/DD/YYYY', 'date:YYYYMMDD', "
            "'phone:digits', 'phone:formatted', 'number', 'currency', "
            "'upper', 'lower', 'strip'. None means pass-through string."
        ),
    )
    service_line_index: Optional[int] = Field(
        default=None,
        description="0-based index into claim.serviceLines for kind=service_line. None otherwise.",
    )
    derive: Optional[DerivedSpec] = Field(
        default=None,
        description="Predicate spec for kind=derived. None otherwise.",
    )
    literal: Optional[str] = Field(
        default=None,
        description="Literal value for kind=constant. None otherwise.",
    )
    reason: str = Field(
        default="",
        description="One-line explanation of why this resolution was chosen (or why unmapped).",
    )


class OptionResolution(BaseModel):
    """For radio_group / checkbox FormFields: when to select one option."""
    option_label: str
    when: Resolution
    selected_if: Literal["truthy", "equals", "always"] = "truthy"
    equals_value: Optional[str] = None


class FieldResolution(BaseModel):
    field_id: str
    confidence: Literal["high", "medium", "low", "unmapped"]
    value: Resolution
    options: list[OptionResolution] = []


class FieldMap(BaseModel):
    """Per-form resolution cache. One file per form; reused across cases."""
    schema_fingerprint: str
    db_schema_fingerprint: str
    form_pdf_basename: str
    created_at: str
    field_resolutions: list[FieldResolution]
    unresolved_field_ids: list[str] = []
    notes: list[str] = []


class LLMResolverResponse(BaseModel):
    """Structured-output wrapper for the LLM batch resolution call."""
    field_resolutions: list[FieldResolution]
    notes: list[str] = []
