"""Agent 3 — PDF writer.

  * anchor_placer.build_initial_plan       deterministic initial coords
  * pdf_writer.correct_plan                N labeled-marker vision passes
  * pdf_writer.apply_plan                  stamps final ops onto the PDF
"""
from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path
from typing import Optional

import pymupdf
from openai import OpenAI
from pydantic import BaseModel, Field

from claims_parser.review_models import ReviewItem, ReviewReport
from claims_parser.writer_models import WriteOp, WritePlan

DEFAULT_MODEL = "gpt-5-mini"
RENDER_DPI = 150

MARKER_RADIUS_PT = 6.0
MARKER_COLOR = (1.0, 0.0, 0.0)
TICK_SIZE_PT = 6.0
TICK_WIDTH = 1.4
LARGE_RESIDUAL_PX = 80.0  # cumulative |dx|+|dy| over all iterations


CORRECTION_SYSTEM_PROMPT = """You are reviewing a printed form on which numbered red circles mark the
proposed (x, y) anchor for stamping a value into a form field.

For each numbered marker:
- Decide whether the marker sits inside the correct input slot (the blank line,
  the box, or the checkbox/circle) for the field described.
- If correctly placed, set ok=true and dx=dy=0.
- Otherwise set ok=false and return the (dx, dy) pixel offset that, when added
  to the current marker position, would move it into the correct slot.

Coordinates are image pixels, top-left origin. Positive dx moves right,
positive dy moves down. Only return entries for markers visible on this page
image. Do not invent markers.
"""


class MarkerCorrection(BaseModel):
    marker_id: int
    ok: bool
    dx: float = Field(description="Horizontal pixel offset to apply. 0 if ok.")
    dy: float = Field(description="Vertical pixel offset to apply. 0 if ok.")


class PageCorrections(BaseModel):
    corrections: list[MarkerCorrection]


def _load_openai_key() -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not found. Add it to parser.env.")
    return key


def _px_to_pt(px: float, dpi: int) -> float:
    return px * 72.0 / dpi


def _draw_tick(page, x_pt: float, y_pt: float, size: float = TICK_SIZE_PT,
               color=(0.0, 0.0, 0.0), width: float = TICK_WIDTH) -> None:
    p1 = (x_pt - size * 0.45, y_pt + size * 0.00)
    p2 = (x_pt - size * 0.10, y_pt + size * 0.35)
    p3 = (x_pt + size * 0.55, y_pt - size * 0.45)
    page.draw_line(p1, p2, color=color, width=width)
    page.draw_line(p2, p3, color=color, width=width)


def _render_with_markers(
    pdf_path: Path,
    plan: WritePlan,
    dpi: int,
) -> tuple[list[bytes], list[tuple[int, int]], dict[int, list[tuple[int, WriteOp]]]]:
    page_to_markers: dict[int, list[tuple[int, WriteOp]]] = {}
    for i, op in enumerate(plan.operations, start=1):
        page_to_markers.setdefault(op.page, []).append((i, op))

    with tempfile.TemporaryDirectory() as tmp:
        annotated = Path(tmp) / "annotated.pdf"
        doc = pymupdf.open(pdf_path)
        try:
            for page_num, items in page_to_markers.items():
                if not (1 <= page_num <= doc.page_count):
                    continue
                page = doc[page_num - 1]
                for marker_id, op in items:
                    pt_x = _px_to_pt(op.x, dpi)
                    pt_y = _px_to_pt(op.y, dpi)
                    page.draw_circle(center=(pt_x, pt_y), radius=MARKER_RADIUS_PT,
                                     color=MARKER_COLOR, width=1.0)
                    page.insert_text(
                        (pt_x + MARKER_RADIUS_PT + 1, pt_y),
                        str(marker_id),
                        fontsize=8,
                        color=MARKER_COLOR,
                    )
            doc.save(annotated)
        finally:
            doc.close()

        pngs: list[bytes] = []
        sizes: list[tuple[int, int]] = []
        rendered = pymupdf.open(annotated)
        try:
            for i in range(rendered.page_count):
                pix = rendered[i].get_pixmap(dpi=dpi)
                pngs.append(pix.tobytes("png"))
                sizes.append((pix.width, pix.height))
        finally:
            rendered.close()

    return pngs, sizes, page_to_markers


def _run_one_pass(
    plan: WritePlan,
    pdf_path: Path,
    client: OpenAI,
    model: str,
    dpi: int,
) -> tuple[list[tuple[int, int]], dict[int, MarkerCorrection]]:
    pngs, sizes, page_to_markers = _render_with_markers(pdf_path, plan, dpi=dpi)
    corrections: dict[int, MarkerCorrection] = {}

    for page_index, png in enumerate(pngs):
        items = page_to_markers.get(page_index + 1)
        if not items:
            continue
        img_w, img_h = sizes[page_index]
        b64 = base64.b64encode(png).decode("ascii")
        marker_summary = "\n".join(
            f"  {mid}: field_id={op.field_id!r} kind={op.kind} "
            f"value={op.text!r} at ({op.x:.0f},{op.y:.0f})"
            for mid, op in items
        )
        user_content = [
            {"type": "text", "text": (
                f"Page {page_index+1}, image {img_w}x{img_h} px.\n"
                f"Markers on this page:\n{marker_summary}"
            )},
            {"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{b64}"
            }},
        ]
        response = client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": CORRECTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format=PageCorrections,
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            continue
        for c in parsed.corrections:
            corrections[c.marker_id] = c
    return sizes, corrections


def correct_plan(
    plan: WritePlan,
    pdf_path: Path,
    model: str = DEFAULT_MODEL,
    client: Optional[OpenAI] = None,
    dpi: int = RENDER_DPI,
    iterations: int = 2,
) -> tuple[WritePlan, list[tuple[int, int]], ReviewReport]:
    if client is None:
        client = OpenAI(api_key=_load_openai_key())

    op_by_marker = {i: op for i, op in enumerate(plan.operations, start=1)}
    cumulative_delta: dict[int, float] = {i: 0.0 for i in op_by_marker}
    last_corrections: dict[int, MarkerCorrection] = {}
    sizes: list[tuple[int, int]] = []

    for _ in range(max(1, iterations)):
        sizes, last_corrections = _run_one_pass(plan, pdf_path, client, model, dpi)
        for mid, c in last_corrections.items():
            op = op_by_marker.get(mid)
            if op is None or c.ok:
                continue
            op.x += c.dx
            op.y += c.dy
            cumulative_delta[mid] += abs(c.dx) + abs(c.dy)

    review_items: list[ReviewItem] = []
    for mid, op in op_by_marker.items():
        c = last_corrections.get(mid)
        if c is not None and not c.ok:
            review_items.append(ReviewItem(
                field_id=op.field_id,
                page=op.page,
                reason="uncertain_after_vision",
                details=f"final dx={c.dx:.0f} dy={c.dy:.0f}",
            ))
            continue
        if cumulative_delta[mid] >= LARGE_RESIDUAL_PX:
            review_items.append(ReviewItem(
                field_id=op.field_id,
                page=op.page,
                reason="large_residual_correction",
                details=f"cumulative |dx|+|dy| ≈ {cumulative_delta[mid]:.0f} px",
            ))

    return plan, sizes, ReviewReport(items=review_items)


def apply_plan(
    plan: WritePlan,
    page_sizes: list[tuple[int, int]],
    pdf_path: Path,
    output_path: Path,
    font_size: float = 10.0,
) -> None:
    doc = pymupdf.open(pdf_path)
    try:
        for op in plan.operations:
            page_index = op.page - 1
            if not (0 <= page_index < doc.page_count):
                continue
            page = doc[page_index]
            if page_index < len(page_sizes):
                img_w, img_h = page_sizes[page_index]
            else:
                img_w = int(page.rect.width * RENDER_DPI / 72)
                img_h = int(page.rect.height * RENDER_DPI / 72)
            scale_x = page.rect.width / img_w
            scale_y = page.rect.height / img_h
            pdf_x = op.x * scale_x
            pdf_y = op.y * scale_y
            if op.kind == "tick":
                _draw_tick(page, pdf_x, pdf_y)
            else:
                page.insert_text(
                    (pdf_x, pdf_y),
                    op.text,
                    fontsize=font_size,
                    color=(0, 0, 0),
                )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_path)
    finally:
        doc.close()


def save_plan(plan: WritePlan, output_path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(plan.model_dump_json(indent=2))


def load_plan(input_path) -> WritePlan:
    return WritePlan.model_validate_json(Path(input_path).read_text())


def save_review(report: ReviewReport, output_path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2))
