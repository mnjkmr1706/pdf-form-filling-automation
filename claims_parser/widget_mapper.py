"""Bind every AcroForm widget to its printed label using a vision LLM.

Chunks the PDF into ≤4-page groups; one structured-output call per chunk.
Each widget ends in `bindings` or `unmapped_widget_field_names`. Spatial
sanity checks demote bad bindings to unmapped.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Optional

import pymupdf
from openai import OpenAI

import re

from claims_parser.mapping_models import (
    LabelSource,
    WidgetBinding,
    WidgetMapping,
    _LLMChunkResponse,
)
from claims_parser.models import DocumentContext, KVP, SelectionMark, TableCell, TextLine
from claims_parser.schema_models import FieldType, FormField, FormSchema
from claims_parser.widget_models import Widget, WidgetCatalog

DEFAULT_MODEL = "gpt-5-mini"
CHUNK_PAGES = 4
RENDER_DPI = 144
SPATIAL_TOLERANCE_PT = 100.0


SYSTEM_PROMPT = """You bind every fillable AcroForm widget on a PDF page to the printed label that
names it on the form.

INPUTS PER CALL
- One or more page images: the authoritative visual rendering of the pages.
- A DI hint set: labels the layout OCR detected on these pages — KVPs,
  text lines, table cells, selection marks. Each carries a polygon in PDF
  points.
- A widget catalog: every input field on these pages with its rect in PDF
  points, widget_type, opaque PDF field_name, optional author-set tu_label,
  on_value, choice_values, and xref.

COORDINATE SYSTEM
- Both page images and PDF polygons use top-left origin with y growing
  downward. All measurements are in PDF points (1 pt = 1/72 inch).
- Page sizes (width, height) in points are provided so you can relate
  image pixels to PDF points.

OUTPUT CONTRACT
- Emit one WidgetBinding per widget OR list the widget's field_name in
  `unmapped_widget_field_names`. Every widget in the input MUST appear in
  exactly one of the two outputs. Never silently drop a widget.
- label_text: the printed label, copied verbatim. Must appear verbatim in
  the DI hint set OR be plainly visible on the page image. Never paraphrase.
- label_source: "kvp", "text_line", or "table_cell" if drawn from the DI
  hint set; "image_only" if the label is visible on the image but missing
  from DI.
- label_polygon: copy verbatim from the DI hint. Null ONLY when
  label_source is "image_only".
- label_page: the page on which the label appears. Must equal the
  widget's page.
- semantic_field_id: lowercase snake_case derived from the label, unique
  within the form. Radio siblings sharing a label share this id.
- confidence: your honest estimate the binding is correct, 0 to 1.
- reasoning: one short sentence naming the evidence (e.g. "label sits
  immediately left of the widget rect and matches DI KVP key").

RADIO GROUPS
- Widgets with widget_type=="radio" sharing the same field_name are option
  siblings of one logical question.
- Emit one binding per sibling widget. Each binding's widget_xref points to
  one specific option widget — that is the distinguishing key.
- All siblings share label_text (the parent question), label_polygon,
  label_page, and semantic_field_id.
- option_label: the printed choice text adjacent to THIS specific option
  widget (e.g. the word next to the "○" — usually "Yes" / "No" / a choice).

CHECKBOXES
- Treat each checkbox widget as its own logical field. Its label_text is
  the printed prompt that names the choice (often the text immediately to
  the right of the box).
- Set option_label only if the checkbox is one of several whose label_text
  is a multi-word prompt and you want to record the short choice text
  separately. Otherwise null.

CHOICE / DROPDOWN / TEXT / SIGNATURE
- Single binding per widget. label_text is the visible label that names
  the input (typically printed left of or above the widget).
- option_label MUST be null.

HARD RULES
1. Every widget in the catalog MUST end in bindings ∪ unmapped_widget_field_names.
2. label_page MUST equal the widget's rect page. Never bind across pages.
3. tu_label is a hint only. The printed label on the page wins.
4. Never paraphrase or normalize label_text. Copy verbatim.
5. Never invent a label that is not present on THIS form's image.
6. Read THIS form only. Do not assume conventions from any other form.
7. If no plausible printed label is visible near a widget, mark it unmapped.
8. Match by spatial adjacency on the image first; confirm against the DI
   hint set.
"""


def _load_openai_key() -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not found. Add it to parser.env.")
    return key


def _render_page_png_b64(doc, page_idx: int, dpi: int) -> str:
    pix = doc[page_idx].get_pixmap(dpi=dpi)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def _di_payload_for_pages(ctx: DocumentContext, pages: set[int]) -> dict:
    return {
        "key_value_pairs": [
            kvp.model_dump() for kvp in ctx.key_value_pairs if kvp.key_page in pages
        ],
        "text_lines": [
            line.model_dump() for line in ctx.lines if line.page in pages
        ],
        "table_cells": [
            c.model_dump()
            for t in ctx.tables for c in t.cells
            if c.page in pages and c.text.strip()
        ],
        "selection_marks": [
            m.model_dump() for m in ctx.selection_marks if m.page in pages
        ],
    }


def _widget_payload(widgets: list[Widget]) -> list[dict]:
    return [w.model_dump() for w in widgets]


def _page_sizes_payload(catalog: WidgetCatalog, pages: list[int]) -> dict:
    return {p: catalog.page_sizes_pt[p - 1] for p in pages}


def _build_user_content(
    doc: pymupdf.Document,
    catalog: WidgetCatalog,
    ctx: DocumentContext,
    chunk_pages: list[int],
    chunk_widgets: list[Widget],
) -> list[dict]:
    pages_set = set(chunk_pages)
    text_payload = {
        "pages": chunk_pages,
        "page_sizes_pt": _page_sizes_payload(catalog, chunk_pages),
        "widgets": _widget_payload(chunk_widgets),
        "di_hints": _di_payload_for_pages(ctx, pages_set),
    }
    content: list[dict] = [
        {"type": "text", "text": json.dumps(text_payload, separators=(",", ":"))},
    ]
    for p in chunk_pages:
        png_b64 = _render_page_png_b64(doc, p - 1, RENDER_DPI)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{png_b64}", "detail": "high"},
        })
    return content


def _rect_label_distance(rect, polygon: list[float]) -> float:
    xs = polygon[0::2]
    ys = polygon[1::2]
    lx0, lx1 = min(xs), max(xs)
    ly0, ly1 = min(ys), max(ys)
    dx = max(rect.x0 - lx1, lx0 - rect.x1, 0.0)
    dy = max(rect.y0 - ly1, ly0 - rect.y1, 0.0)
    return (dx * dx + dy * dy) ** 0.5


def _validate_chunk(
    response: _LLMChunkResponse,
    chunk_widgets: list[Widget],
) -> tuple[list[WidgetBinding], list[str]]:
    catalog_by_xref = {w.xref: w for w in chunk_widgets}
    catalog_by_name = {w.field_name: w for w in chunk_widgets}
    seen_xrefs: set[int] = set()
    valid: list[WidgetBinding] = []
    unmapped: list[str] = list(response.unmapped_widget_field_names)

    for b in response.bindings:
        w = catalog_by_xref.get(b.widget_xref) or catalog_by_name.get(b.widget_field_name)
        if w is None:
            continue
        if w.xref in seen_xrefs:
            continue
        if b.label_page != w.rect.page:
            unmapped.append(w.field_name)
            seen_xrefs.add(w.xref)
            continue
        if b.label_source != "image_only" and b.label_polygon:
            if _rect_label_distance(w.rect, b.label_polygon) > SPATIAL_TOLERANCE_PT:
                b = b.model_copy(update={"confidence": max(0.0, b.confidence - 0.2)})
        b = b.model_copy(update={
            "widget_xref": w.xref,
            "widget_field_name": w.field_name,
            "on_value": w.on_value if w.widget_type in ("checkbox", "radio") else None,
        })
        valid.append(b)
        seen_xrefs.add(w.xref)

    for w in chunk_widgets:
        if w.xref not in seen_xrefs and w.field_name not in unmapped:
            unmapped.append(w.field_name)

    unmapped = list(dict.fromkeys(unmapped))
    return valid, unmapped


def map_widgets(
    pdf_path: str | Path,
    doc_ctx: DocumentContext,
    catalog: WidgetCatalog,
    model: str = DEFAULT_MODEL,
    client: Optional[OpenAI] = None,
) -> WidgetMapping:
    pdf_path = Path(pdf_path)
    if client is None:
        client = OpenAI(api_key=_load_openai_key())

    widgets_by_page: dict[int, list[Widget]] = {}
    for w in catalog.widgets:
        widgets_by_page.setdefault(w.rect.page, []).append(w)

    all_bindings: list[WidgetBinding] = []
    all_unmapped: list[str] = []
    chunk_count = 0

    doc = pymupdf.open(pdf_path)
    try:
        for start in range(1, catalog.page_count + 1, CHUNK_PAGES):
            pages = list(range(start, min(start + CHUNK_PAGES, catalog.page_count + 1)))
            chunk_widgets = [w for p in pages for w in widgets_by_page.get(p, [])]
            if not chunk_widgets:
                continue
            chunk_count += 1

            user_content = _build_user_content(doc, catalog, doc_ctx, pages, chunk_widgets)
            response = client.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format=_LLMChunkResponse,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                all_unmapped.extend(w.field_name for w in chunk_widgets)
                continue
            valid, unmapped = _validate_chunk(parsed, chunk_widgets)
            all_bindings.extend(valid)
            all_unmapped.extend(unmapped)
    finally:
        doc.close()

    return WidgetMapping(
        file_name=pdf_path.name,
        model=model,
        chunk_count=chunk_count,
        bindings=all_bindings,
        unmapped_widget_field_names=list(dict.fromkeys(all_unmapped)),
    )


LONG_TEXT_MIN_HEIGHT_PT = 30.0


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


def _type_and_options(w: Widget, b: WidgetBinding) -> tuple[FieldType, Optional[list[str]]]:
    if w.widget_type == "checkbox":
        return "checkbox", [b.label_text]
    if w.widget_type == "choice":
        return "radio_group", list(w.choice_values or [])
    if w.widget_type == "signature":
        return "signature", None
    height = w.rect.y1 - w.rect.y0
    return ("long_text" if height > LONG_TEXT_MIN_HEIGHT_PT else "text"), None


def build_schema_from_mapping(
    catalog: WidgetCatalog,
    mapping: WidgetMapping,
) -> FormSchema:
    catalog_by_xref = {w.xref: w for w in catalog.widgets}
    groups: dict[str, list[WidgetBinding]] = {}
    for b in mapping.bindings:
        groups.setdefault(b.semantic_field_id, []).append(b)

    taken: set[str] = set()
    fields: list[FormField] = []

    for raw_id, group_bindings in groups.items():
        widgets = [catalog_by_xref.get(b.widget_xref) for b in group_bindings]
        widgets = [w for w in widgets if w is not None]
        if not widgets:
            continue
        types = {w.widget_type for w in widgets}

        if types == {"radio"} and len(group_bindings) > 1:
            label = group_bindings[0].label_text
            options = [
                b.option_label or b.on_value or "Option"
                for b in group_bindings
            ]
            fields.append(FormField(
                field_id=_uniqify(_snake(raw_id), taken),
                label=label,
                section=None,
                field_type="radio_group",
                options=options,
                format_hint=None,
                required=False,
                source_text=label,
                widget_bindings=group_bindings,
            ))
        else:
            for b, w in zip(group_bindings, widgets):
                ftype, options = _type_and_options(w, b)
                fields.append(FormField(
                    field_id=_uniqify(_snake(b.semantic_field_id), taken),
                    label=b.label_text,
                    section=None,
                    field_type=ftype,
                    options=options,
                    format_hint=None,
                    required=False,
                    source_text=b.label_text,
                    widget_bindings=[b],
                ))

    return FormSchema(
        form_title=None,
        issuer=None,
        sections=[],
        fields=fields,
    )


def save_mapping(mapping: WidgetMapping, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(mapping.model_dump_json(indent=2))
    return output_path


def load_mapping(input_path: str | Path) -> WidgetMapping:
    return WidgetMapping.model_validate_json(Path(input_path).read_text())
