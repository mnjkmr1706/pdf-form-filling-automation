"""Agent 3 equivalent for editable forms.

Writes values into AcroForm widgets directly via pymupdf. Output PDF stays
editable unless `flatten=True` is passed. No anchor math, no vision passes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pymupdf

from claims_parser.acroform_models import AcroFormBinding, AcroFormFieldBinding
from claims_parser.filler_models import FilledFormSchema
from claims_parser.review_models import ReviewItem, ReviewReport


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


def _checkbox_is_selected(value) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, str):
        return value.strip() != ""
    return False


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def write_acroform(
    pdf_path: str | Path,
    filled: FilledFormSchema,
    binding: AcroFormBinding,
    output_path: str | Path,
    flatten: bool = False,
) -> ReviewReport:
    pdf_path = Path(pdf_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    binding_by_id = {b.field_id: b for b in binding.fields}
    review: list[ReviewItem] = []

    doc = pymupdf.open(pdf_path)
    try:
        for field in filled.fields:
            b: Optional[AcroFormFieldBinding] = binding_by_id.get(field.field_id)
            if b is None:
                review.append(ReviewItem(
                    field_id=field.field_id, label=field.label, section=field.section,
                    reason="missing_binding",
                ))
                continue

            if field.value in (None, "", []):
                review.append(ReviewItem(
                    field_id=field.field_id, label=field.label, section=field.section,
                    page=b.page, reason="missing_value",
                ))
                continue

            cat = b.widget_category

            if cat == "radio_group":
                if not b.options:
                    review.append(ReviewItem(
                        field_id=field.field_id, label=field.label, page=b.page,
                        reason="missing_binding", details="radio_group has no option bindings",
                    ))
                    continue
                target = _norm(field.value if isinstance(field.value, str) else "")
                opt = next((o for o in b.options if _norm(o.label) == target), None)
                if opt is None:
                    opt = next(
                        (o for o in b.options
                         if target in _norm(o.label) or _norm(o.label) in target),
                        None,
                    )
                if opt is None:
                    review.append(ReviewItem(
                        field_id=field.field_id, label=field.label, page=b.page,
                        reason="unsettable_widget",
                        details=f"no option binding for value {field.value!r}",
                    ))
                    continue
                page = doc[opt.page - 1]
                w = _find_widget(page, opt.widget_xref)
                if w is None or not _set_button_on(w, opt.on_value):
                    review.append(ReviewItem(
                        field_id=field.field_id, label=field.label, page=opt.page,
                        reason="unsettable_widget",
                    ))
                continue

            page = doc[b.page - 1]
            if b.widget_xref is None:
                review.append(ReviewItem(
                    field_id=field.field_id, label=field.label, page=b.page,
                    reason="missing_binding",
                ))
                continue
            w = _find_widget(page, b.widget_xref)
            if w is None:
                review.append(ReviewItem(
                    field_id=field.field_id, label=field.label, page=b.page,
                    reason="missing_binding", details="widget xref not found on page",
                ))
                continue

            if cat == "checkbox":
                if _checkbox_is_selected(field.value):
                    if not _set_button_on(w, b.on_value):
                        review.append(ReviewItem(
                            field_id=field.field_id, label=field.label, page=b.page,
                            reason="unsettable_widget",
                        ))
            elif cat == "signature":
                if not _set_text(w, field.value):
                    review.append(ReviewItem(
                        field_id=field.field_id, label=field.label, page=b.page,
                        reason="unsettable_widget",
                        details="signature widget did not accept text value",
                    ))
            else:  # text, choice
                if not _set_text(w, field.value):
                    review.append(ReviewItem(
                        field_id=field.field_id, label=field.label, page=b.page,
                        reason="unsettable_widget",
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
