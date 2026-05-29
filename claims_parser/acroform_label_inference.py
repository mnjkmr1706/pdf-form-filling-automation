"""Infer human-readable labels for AcroForm widgets.

Cascade:
  Tier 1  widget.field_label (TU entry / tooltip) when non-empty and not the
          opaque field_name.
  Tier 2  proximity match against text-layer words (no OCR; pymupdf
          page.get_text("words")), with placement rules per widget category.
  Tier 3  optional single vision pass per page that still has unresolved
          widgets after Tiers 1-2.
"""
from __future__ import annotations

import base64
import os
from typing import Optional

import pymupdf
from openai import OpenAI
from pydantic import BaseModel


WordTuple = tuple[float, float, float, float, str]  # (x0, y0, x1, y1, text)

DEFAULT_VISION_MODEL = "gpt-5-mini"
RENDER_DPI = 150
ROW_OVERLAP_FRAC = 0.3   # vertical overlap to count as same row
ADJ_GAP_PX = 40.0        # max horizontal gap within one label phrase
ABOVE_MAX_DIST = 60.0    # max vertical distance for "label above"


def _vertical_overlap(a_y0: float, a_y1: float, b_y0: float, b_y1: float) -> float:
    return max(0.0, min(a_y1, b_y1) - max(a_y0, b_y0))


def _clean(label: str) -> str:
    return label.strip().rstrip(":").strip()


def _label_left_of(rect, words: list[WordTuple]) -> Optional[str]:
    wx0, wy0, wx1, wy1 = rect
    h = max(wy1 - wy0, 1.0)
    row = [w for w in words
           if _vertical_overlap(wy0, wy1, w[1], w[3]) > h * ROW_OVERLAP_FRAC
           and w[2] <= wx0 + 2.0]
    if not row:
        return None
    row.sort(key=lambda w: w[0])
    out: list[WordTuple] = []
    for w in reversed(row):
        if out and out[-1][0] - w[2] > ADJ_GAP_PX:
            break
        out.append(w)
    out.reverse()
    return _clean(" ".join(w[4] for w in out)) or None


def _label_right_of(rect, words: list[WordTuple]) -> Optional[str]:
    wx0, wy0, wx1, wy1 = rect
    h = max(wy1 - wy0, 1.0)
    row = [w for w in words
           if _vertical_overlap(wy0, wy1, w[1], w[3]) > h * ROW_OVERLAP_FRAC
           and w[0] >= wx1 - 2.0]
    if not row:
        return None
    row.sort(key=lambda w: w[0])
    out: list[WordTuple] = []
    for w in row:
        if out and w[0] - out[-1][2] > ADJ_GAP_PX:
            break
        out.append(w)
    return _clean(" ".join(w[4] for w in out)) or None


def _label_above(rect, words: list[WordTuple]) -> Optional[str]:
    wx0, wy0, wx1, wy1 = rect
    above = [w for w in words
             if w[3] <= wy0 + 1.0
             and (wy0 - w[3]) < ABOVE_MAX_DIST
             and w[2] > wx0 - 10.0
             and w[0] < wx1 + 10.0]
    if not above:
        return None
    closest = max(w[3] for w in above)
    line = [w for w in above if closest - w[3] < 4.0]
    line.sort(key=lambda w: w[0])
    return _clean(" ".join(w[4] for w in line)) or None


def _is_button(widget) -> bool:
    return (widget.field_type_string or "").lower() in ("checkbox", "radiobutton")


def _tu_label(widget) -> Optional[str]:
    label = (widget.field_label or "").strip()
    if not label:
        return None
    name = (widget.field_name or "").strip()
    if label.lower() == name.lower():
        return None
    return _clean(label) or None


def infer_label_for_widget(widget, words: list[WordTuple]) -> tuple[Optional[str], str]:
    """Returns (label, source) where source is one of 'tu', 'proximity', 'none'."""
    tu = _tu_label(widget)
    if tu:
        return tu, "tu"

    r = widget.rect
    rect = (r.x0, r.y0, r.x1, r.y1)
    h = r.y1 - r.y0

    if _is_button(widget):
        label = _label_right_of(rect, words) or _label_left_of(rect, words)
    elif h > 30:
        label = _label_above(rect, words) or _label_left_of(rect, words)
    else:
        label = _label_left_of(rect, words) or _label_above(rect, words)

    return (label, "proximity") if label else (None, "none")


def infer_label_for_rect(rect, words: list[WordTuple], assume_button: bool = False) -> Optional[str]:
    """Used for radio-group bounding rects: try above first, then left."""
    if assume_button:
        return _label_right_of(rect, words) or _label_above(rect, words) or _label_left_of(rect, words)
    return _label_above(rect, words) or _label_left_of(rect, words)


# -----------------------------------------------------------------------------
# Tier 3 — vision residual pass
# -----------------------------------------------------------------------------

class _VisionLabelEntry(BaseModel):
    marker_id: int
    label: str


class _VisionLabelResponse(BaseModel):
    labels: list[_VisionLabelEntry]


VISION_SYSTEM_PROMPT = """You are given a rendered page of a printed form with numbered red
rectangles drawn over a subset of input widgets whose labels could not be
inferred automatically. For each numbered rectangle, return the human-readable
label printed on the form for that input (e.g. 'Provider Name', 'Date of
Service', 'Patient Address'). Do not invent labels. Return only entries for
the numbered rectangles visible on this page.
"""


def _load_openai_key() -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not found. Add it to parser.env.")
    return key


def vision_label_residuals(
    pdf_path,
    unresolved_by_page: dict[int, list[tuple[int, tuple[float, float, float, float]]]],
    model: str = DEFAULT_VISION_MODEL,
    client: Optional[OpenAI] = None,
    dpi: int = RENDER_DPI,
) -> dict[int, str]:
    """For each page that has unresolved widgets, draw numbered red rectangles
    on a temp PDF copy, render, and ask the model for labels per marker.

    `unresolved_by_page` maps 1-indexed page number -> list of
    (marker_id, (x0, y0, x1, y1)) in PDF points.

    Returns: dict marker_id -> label.
    """
    if not unresolved_by_page:
        return {}
    if client is None:
        client = OpenAI(api_key=_load_openai_key())

    src = pymupdf.open(pdf_path)
    out: dict[int, str] = {}
    try:
        for page_num, items in unresolved_by_page.items():
            if not (1 <= page_num <= src.page_count):
                continue
            page = src[page_num - 1]

            doc = pymupdf.open()
            doc.insert_pdf(src, from_page=page_num - 1, to_page=page_num - 1)
            try:
                p = doc[0]
                for mid, rect in items:
                    p.draw_rect(pymupdf.Rect(*rect), color=(1.0, 0.0, 0.0), width=1.2)
                    p.insert_text(
                        (rect[2] + 2, rect[1] - 2),
                        str(mid),
                        fontsize=9,
                        color=(1.0, 0.0, 0.0),
                    )
                pix = p.get_pixmap(dpi=dpi)
                png = pix.tobytes("png")
            finally:
                doc.close()

            b64 = base64.b64encode(png).decode("ascii")
            user_content = [
                {"type": "text", "text": (
                    f"Page {page_num}. Numbered red rectangles: "
                    + ", ".join(str(m) for m, _ in items)
                )},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{b64}"
                }},
            ]
            response = client.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": VISION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format=_VisionLabelResponse,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                continue
            for entry in parsed.labels:
                cleaned = _clean(entry.label)
                if cleaned:
                    out[entry.marker_id] = cleaned
    finally:
        src.close()
    return out
