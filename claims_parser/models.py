"""Pydantic models for the document context returned by Azure extraction.

A `DocumentContext` is the serializable handoff between the Azure extractor and
the downstream agents (schema-builder, filler, PDF-writer). It strips
Azure-specific SDK types but preserves all spatial info (bounding polygons)
needed for writing values back into the PDF.
"""
from typing import Optional

from pydantic import BaseModel, Field


Polygon = list[float]  # flat list of 8 floats: [x1, y1, x2, y2, x3, y3, x4, y4]


class TextLine(BaseModel):
    text: str
    page: int
    polygon: Polygon


class KVP(BaseModel):
    key_text: str
    key_page: Optional[int] = None
    key_polygon: Optional[Polygon] = None
    value_text: Optional[str] = None
    value_page: Optional[int] = None
    value_polygon: Optional[Polygon] = None
    confidence: Optional[float] = None


class TableCell(BaseModel):
    row: int
    column: int
    text: str
    page: int
    polygon: Optional[Polygon] = None
    row_span: int = 1
    column_span: int = 1
    kind: Optional[str] = None


class Table(BaseModel):
    page: int
    row_count: int
    column_count: int
    cells: list[TableCell]


class SelectionMark(BaseModel):
    page: int
    state: str  # "selected" | "unselected"
    polygon: Polygon
    confidence: Optional[float] = None


class PageMetadata(BaseModel):
    page_number: int
    width: float
    height: float
    unit: str
    line_count: int


class DocumentContext(BaseModel):
    file_name: str
    file_path: str
    model_id: str = Field(description="The Azure model used, e.g. 'prebuilt-layout'.")
    full_text: str = Field(description="The concatenated OCR text of the entire document.")
    pages: list[PageMetadata]
    lines: list[TextLine]
    tables: list[Table]
    key_value_pairs: list[KVP]
    selection_marks: list[SelectionMark] = []
