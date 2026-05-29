"""Deterministic initial placement using Azure polygons.

Maps each FilledFormField to one or more WriteOps in image-pixel space (top-left
origin) at the same DPI used for rendering. No LLM calls.

Azure polygons are in page.unit (almost always 'inch'). We convert to pixels
via dpi multiplication. apply_plan() converts pixels back to PDF points.
"""
from __future__ import annotations

from typing import Optional

from claims_parser.filler_models import FilledFormSchema, FilledFormField
from claims_parser.models import DocumentContext, KVP, Polygon, SelectionMark, TextLine
from claims_parser.writer_models import WriteOp, WritePlan

LABEL_PAD_INCH = 0.05  # horizontal gap from end-of-label to value start
BASELINE_LIFT_INCH = 0.02  # raise the text baseline slightly above the box bottom


def _bounds(polygon: Polygon) -> tuple[float, float, float, float]:
    xs = polygon[0::2]
    ys = polygon[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _center(polygon: Polygon) -> tuple[float, float]:
    xs = polygon[0::2]
    ys = polygon[1::2]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _norm(s: str) -> str:
    return " ".join(s.strip().lower().split()).rstrip(":")


def _find_kvp_anchor(needle: str, ctx: DocumentContext) -> Optional[KVP]:
    n = _norm(needle)
    if not n:
        return None
    candidates = [k for k in ctx.key_value_pairs if k.key_polygon]
    for k in candidates:
        if _norm(k.key_text) == n:
            return k
    for k in candidates:
        kn = _norm(k.key_text)
        if n in kn or kn in n:
            return k
    return None


def _find_line_anchor(needle: str, ctx: DocumentContext) -> Optional[TextLine]:
    n = _norm(needle)
    if not n:
        return None
    for ln in ctx.lines:
        if _norm(ln.text) == n:
            return ln
    for ln in ctx.lines:
        if n in _norm(ln.text):
            return ln
    return None


def _find_option_mark(
    option: str,
    page: int,
    label_poly: Polygon,
    ctx: DocumentContext,
) -> Optional[SelectionMark]:
    """Find the selection mark on `page` belonging to the line that contains
    `option`, preferring marks vertically close to the field label."""
    n = _norm(option)
    if not n:
        return None
    opt_lines = [
        ln for ln in ctx.lines
        if ln.page == page and n in _norm(ln.text)
    ]
    if not opt_lines:
        return None
    label_cy = _center(label_poly)[1]
    opt_line = min(opt_lines, key=lambda ln: abs(_center(ln.polygon)[1] - label_cy))
    line_cx, line_cy = _center(opt_line.polygon)
    marks = [m for m in ctx.selection_marks if m.page == page]
    if not marks:
        return None
    return min(marks, key=lambda m: (
        (_center(m.polygon)[0] - line_cx) ** 2
        + (_center(m.polygon)[1] - line_cy) ** 2
    ))


def _place_text(field: FilledFormField, kvp: KVP, dpi: int) -> Optional[WriteOp]:
    if not isinstance(field.value, str) or not field.value:
        return None
    if kvp.value_polygon:
        x_in, _, _, y_bot_in = _bounds(kvp.value_polygon)
    else:
        _, _, x_right_in, y_bot_in = _bounds(kvp.key_polygon)  # type: ignore[arg-type]
        x_in = x_right_in + LABEL_PAD_INCH
    y_in = y_bot_in - BASELINE_LIFT_INCH
    return WriteOp(
        field_id=field.field_id,
        page=kvp.key_page or 1,
        text=field.value,
        x=x_in * dpi,
        y=y_in * dpi,
    )


def _place_text_from_line(field: FilledFormField, line: TextLine, dpi: int) -> Optional[WriteOp]:
    if not isinstance(field.value, str) or not field.value:
        return None
    _, _, x_right_in, y_bot_in = _bounds(line.polygon)
    return WriteOp(
        field_id=field.field_id,
        page=line.page,
        text=field.value,
        x=(x_right_in + LABEL_PAD_INCH) * dpi,
        y=(y_bot_in - BASELINE_LIFT_INCH) * dpi,
    )


def _place_choice(
    field: FilledFormField,
    page: int,
    label_poly: Polygon,
    ctx: DocumentContext,
    dpi: int,
) -> list[WriteOp]:
    if field.value is None:
        return []
    selected = field.value if isinstance(field.value, list) else [field.value]
    ops: list[WriteOp] = []
    for opt in selected:
        if not opt:
            continue
        mark = _find_option_mark(opt, page, label_poly, ctx)
        if not mark:
            continue
        cx, cy = _center(mark.polygon)
        ops.append(WriteOp(
            field_id=field.field_id,
            page=page,
            kind="tick",
            x=cx * dpi,
            y=cy * dpi,
        ))
    return ops


def build_initial_plan(
    filled: FilledFormSchema,
    ctx: DocumentContext,
    dpi: int = 150,
) -> tuple[WritePlan, list[str]]:
    """Returns (plan, unanchored_field_ids)."""
    ops: list[WriteOp] = []
    unanchored: list[str] = []

    for field in filled.fields:
        if field.value in (None, "", []):
            continue

        kvp = _find_kvp_anchor(field.source_text, ctx)
        line = None
        if kvp:
            page = kvp.key_page or 1
            label_poly = kvp.key_polygon  # type: ignore[assignment]
        else:
            line = _find_line_anchor(field.source_text, ctx)
            if not line:
                unanchored.append(field.field_id)
                continue
            page = line.page
            label_poly = line.polygon

        if field.field_type in ("checkbox", "radio_group"):
            choice_ops = _place_choice(field, page, label_poly, ctx, dpi)
            if not choice_ops:
                unanchored.append(field.field_id)
            ops.extend(choice_ops)
        else:
            op = _place_text(field, kvp, dpi) if kvp else _place_text_from_line(field, line, dpi)  # type: ignore[arg-type]
            if op is None:
                unanchored.append(field.field_id)
            else:
                ops.append(op)

    return WritePlan(operations=ops), unanchored
