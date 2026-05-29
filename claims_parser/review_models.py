from typing import Literal, Optional

from pydantic import BaseModel

ReviewReason = Literal[
    "missing_value",                  # filler produced no value
    "unanchored",                     # no KVP/line/selection-mark match -> no marker placed
    "uncertain_after_vision",         # final vision pass still reports the marker not-ok
    "large_residual_correction",      # cumulative correction across passes exceeded threshold
    "label_inference_failed",         # AcroForm: could not infer a label for a widget
    "missing_binding",                # AcroForm: filled field has no widget binding
    "unsettable_widget",              # AcroForm: widget rejected the value (rare)
    "widget_unmapped",                # mapped AcroForm: mapper could not bind a widget
    "widget_low_confidence",          # mapped AcroForm: binding confidence below threshold
    "duplicate_label_match",          # mapped AcroForm: two widgets share semantic_field_id + page
    "radio_option_count_mismatch",    # mapped AcroForm: bound options != catalog options
]


class ReviewItem(BaseModel):
    field_id: str
    label: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    reason: ReviewReason
    details: Optional[str] = None


class ReviewReport(BaseModel):
    items: list[ReviewItem]

    def by_reason(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in self.items:
            out[it.reason] = out.get(it.reason, 0) + 1
        return out
