"""Write filled values into an AcroForm PDF using the bindings baked into FormFields.

Mirrors acroform_writer's interface but consumes `field.widget_bindings`
(populated by the mapped branch) instead of a separate AcroFormBinding file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pymupdf

from claims_parser.filler_models import FilledFormSchema
from claims_parser.mapping_models import WidgetBinding
from claims_parser.review_models import ReviewItem, ReviewReport

LOW_CONFIDENCE_THRESHOLD = 0.5


def _find_widget(page, xref: int):
    for w in page.widgets() or []:
        if w.xref == xref:
            return w
    return None


def _set_text(widget, value) -> bool:
    try:
        widget.field_value = "" if value is None else str(value)
        widget.update()
        return True
    except Exception:
        return False


def _set_button_on(widget, on_value: Optional[str]) -> bool:
    try:
        widget.field_value = on_value if on_value else True
        widget.update()
        return True
    except Exception:
        return False


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _checkbox_is_selected(value, label: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        if not value:
            return False
        return any(_norm(v) == _norm(label) for v in value) or bool(value)
    if isinstance(value, str):
        return value.strip() != ""
    return False


def _select_radio_binding(
    bindings: list[WidgetBinding],
    value,
) -> Optional[WidgetBinding]:
    target = _norm(value if isinstance(value, str) else "")
    if not target:
        return None
    for b in bindings:
        if _norm(b.option_label) == target or _norm(b.on_value) == target:
            return b
    for b in bindings:
        ol = _norm(b.option_label)
        if ol and (target in ol or ol in target):
            return b
    return None


def write_mapped(
    pdf_path: str | Path,
    filled: FilledFormSchema,
    output_path: str | Path,
    flatten: bool = False,
) -> ReviewReport:
    pdf_path = Path(pdf_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    review: list[ReviewItem] = []

    doc = pymupdf.open(pdf_path)
    try:
        for field in filled.fields:
            bindings = field.widget_bindings or []
            if not bindings:
                review.append(ReviewItem(
                    field_id=field.field_id, label=field.label, section=field.section,
                    reason="widget_unmapped",
                ))
                continue

            if field.value in (None, "", []):
                review.append(ReviewItem(
                    field_id=field.field_id, label=field.label, section=field.section,
                    page=bindings[0].label_page, reason="missing_value",
                ))
                continue

            min_conf = min(b.confidence for b in bindings)
            if min_conf < LOW_CONFIDENCE_THRESHOLD:
                review.append(ReviewItem(
                    field_id=field.field_id, label=field.label, section=field.section,
                    page=bindings[0].label_page, reason="widget_low_confidence",
                    details=f"min binding confidence={min_conf:.2f}",
                ))

            if field.field_type == "radio_group" and len(bindings) > 1:
                chosen = _select_radio_binding(bindings, field.value)
                if chosen is None:
                    review.append(ReviewItem(
                        field_id=field.field_id, label=field.label,
                        page=bindings[0].label_page, reason="unsettable_widget",
                        details=f"no option binding matches value {field.value!r}",
                    ))
                    continue
                # Setting field_value on a single radio child updates the parent /V
                # but leaves stale /AS on the other siblings, so PDF viewers render
                # no dot. Explicitly write each sibling's /AS by assigning "Off" +
                # update(); set the chosen one LAST so the parent /V ends up at the
                # chosen on-value.
                for b in bindings:
                    if b.widget_xref == chosen.widget_xref:
                        continue
                    sib_page = doc[b.label_page - 1]
                    sib_w = _find_widget(sib_page, b.widget_xref)
                    if sib_w is None:
                        continue
                    try:
                        sib_w.field_value = "Off"
                        sib_w.update()
                    except Exception:
                        pass
                page = doc[chosen.label_page - 1]
                w = _find_widget(page, chosen.widget_xref)
                if w is None or not _set_button_on(w, chosen.on_value):
                    review.append(ReviewItem(
                        field_id=field.field_id, label=field.label,
                        page=chosen.label_page, reason="unsettable_widget",
                    ))
                continue

            b = bindings[0]
            page = doc[b.label_page - 1]
            w = _find_widget(page, b.widget_xref)
            if w is None:
                review.append(ReviewItem(
                    field_id=field.field_id, label=field.label,
                    page=b.label_page, reason="widget_unmapped",
                    details="widget xref not found at write time",
                ))
                continue

            if field.field_type == "checkbox":
                if _checkbox_is_selected(field.value, field.label):
                    if not _set_button_on(w, b.on_value):
                        review.append(ReviewItem(
                            field_id=field.field_id, label=field.label,
                            page=b.label_page, reason="unsettable_widget",
                        ))
                continue

            if field.field_type == "radio_group":
                if not _set_text(w, field.value):
                    review.append(ReviewItem(
                        field_id=field.field_id, label=field.label,
                        page=b.label_page, reason="unsettable_widget",
                    ))
                continue

            if not _set_text(w, field.value):
                review.append(ReviewItem(
                    field_id=field.field_id, label=field.label,
                    page=b.label_page, reason="unsettable_widget",
                ))

        if flatten:
            try:
                doc.bake()
            except Exception:
                pass

        doc.save(output_path, incremental=False, deflate=True)
    finally:
        doc.close()

    return ReviewReport(items=review)
