"""LLM-produced binding between AcroForm widgets and their printed labels.

Every widget in the source catalog must appear either in `bindings` or in
`unmapped_widget_field_names` — never silently dropped.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from claims_parser.models import Polygon


LabelSource = Literal["kvp", "text_line", "table_cell", "image_only"]


class WidgetBinding(BaseModel):
    widget_field_name: str = Field(description="Matches Widget.field_name in the catalog.")
    widget_xref: int = Field(description="Matches Widget.xref in the catalog. Canonical writer-side identity.")
    semantic_field_id: str = Field(
        description=(
            "Lowercase snake_case identifier derived from the printed label. "
            "Shared across siblings of the same radio group."
        )
    )
    label_text: str = Field(
        description=(
            "The verbatim printed label this widget is bound to. For radio-group "
            "siblings, this is the parent question, identical across siblings."
        )
    )
    label_source: LabelSource = Field(
        description=(
            "'kvp' / 'text_line' / 'table_cell' if drawn from the DI hint set; "
            "'image_only' if visible on the page image but not in DI hints."
        )
    )
    label_polygon: Optional[Polygon] = Field(
        default=None,
        description="Polygon of label_text. Null only when label_source == 'image_only'.",
    )
    label_page: int = Field(description="Must equal the widget's page.")
    confidence: float = Field(description="Self-reported confidence in the binding, 0 to 1.")
    reasoning: str = Field(description="One short sentence explaining what evidence supports the binding.")
    option_label: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable choice text printed next to this specific radio/checkbox option. "
            "Null for text/choice/signature widgets and for checkboxes whose label_text already "
            "names the choice."
        ),
    )
    on_value: Optional[str] = Field(
        default=None,
        description=(
            "PDF export value used to set this widget on. Populated by the validator from the "
            "catalog after the LLM returns; the LLM never produces this field."
        ),
    )


class WidgetMapping(BaseModel):
    file_name: str
    model: str
    chunk_count: int
    bindings: list[WidgetBinding]
    unmapped_widget_field_names: list[str]


class _LLMChunkResponse(BaseModel):
    """Per-chunk LLM response shape. Internal."""
    bindings: list[WidgetBinding]
    unmapped_widget_field_names: list[str]
