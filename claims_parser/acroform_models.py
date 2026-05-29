"""Models for the AcroForm branch.

A binding maps a FormField.field_id back to the AcroForm widget(s) that the
writer must update. Saved alongside the FormSchema in output/intermediate/.
"""
from typing import Literal, Optional

from pydantic import BaseModel


WidgetCategory = Literal["text", "checkbox", "radio_group", "choice", "signature"]


class OptionBinding(BaseModel):
    label: str                       # inferred human-readable option label
    widget_xref: int                 # PDF xref of the widget for this option
    widget_name: str                 # full AcroForm field name
    on_value: str                    # value to assign to set this option "on"
    page: int


class AcroFormFieldBinding(BaseModel):
    field_id: str                    # matches FormField.field_id
    widget_category: WidgetCategory  # how to write this field
    page: int                        # 1-indexed; for radio groups: page of first option
    # Single-widget fields (text / choice / signature / single checkbox):
    widget_xref: Optional[int] = None
    widget_name: Optional[str] = None
    rect: Optional[list[float]] = None
    on_value: Optional[str] = None   # only for single checkbox
    # Multi-widget fields (radio_group):
    options: Optional[list[OptionBinding]] = None


class AcroFormBinding(BaseModel):
    file_name: str
    fields: list[AcroFormFieldBinding]
