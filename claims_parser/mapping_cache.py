"""Form fingerprint for the mapped AcroForm branch.

A stable hash of the WidgetCatalog used to key the mapping cache. Independent
of xref renumbering and sub-point rect jitter.
"""
from __future__ import annotations

import hashlib
import json

from claims_parser.widget_models import Widget, WidgetCatalog

FINGERPRINT_VERSION = 1
RECT_BUCKET_PT = 1.0  # round(v/1.0)*1.0 collapses jitter up to ±0.5pt into the same bucket


def _bucket(v: float) -> float:
    return round(v / RECT_BUCKET_PT) * RECT_BUCKET_PT


def _widget_key(w: Widget) -> tuple:
    return (
        w.rect.page,
        w.field_name,
        w.on_value or "",
        _bucket(w.rect.x0),
        _bucket(w.rect.y0),
    )


def _fingerprint_payload(catalog: WidgetCatalog) -> dict:
    widgets_sorted = sorted(catalog.widgets, key=_widget_key)
    return {
        "v": FINGERPRINT_VERSION,
        "page_count": catalog.page_count,
        "page_sizes_pt": [[_bucket(w), _bucket(h)] for (w, h) in catalog.page_sizes_pt],
        "widgets": [
            {
                "field_name": w.field_name,
                "widget_type": w.widget_type,
                "page": w.rect.page,
                "on_value": w.on_value,
                "choice_values": list(w.choice_values) if w.choice_values else None,
                "rect": [
                    _bucket(w.rect.x0),
                    _bucket(w.rect.y0),
                    _bucket(w.rect.x1),
                    _bucket(w.rect.y1),
                ],
            }
            for w in widgets_sorted
        ],
    }


def compute_form_fingerprint(catalog: WidgetCatalog) -> str:
    payload = _fingerprint_payload(catalog)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
