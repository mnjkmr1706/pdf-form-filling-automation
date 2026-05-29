"""AcroForm widget catalog produced by pymupdf, consumed by the widget mapper.

Each radio-group option is its own Widget (it shares `field_name` with its
siblings). The mapper produces one binding per such widget.
"""
from typing import Literal, Optional

from pydantic import BaseModel


WidgetType = Literal["text", "checkbox", "radio", "choice", "signature", "button"]


class WidgetRect(BaseModel):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class Widget(BaseModel):
    field_name: str
    widget_type: WidgetType
    rect: WidgetRect
    xref: int
    tu_label: Optional[str] = None
    choice_values: Optional[list[str]] = None
    on_value: Optional[str] = None


class WidgetCatalog(BaseModel):
    file_name: str
    page_count: int
    page_sizes_pt: list[tuple[float, float]]
    widgets: list[Widget]
