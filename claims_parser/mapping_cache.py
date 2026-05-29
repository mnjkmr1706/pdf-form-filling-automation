"""Form-fingerprint cache for the mapped AcroForm branch.

Lookup is keyed on a stable hash of the WidgetCatalog (independent of xref
renumbering and sub-point rect jitter). On a hit, the cached binding's
widget_xref values are rebound to the current PDF's catalog before being
handed off to downstream consumers.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from claims_parser.mapping_cache_models import (
    CachedMapping,
    CacheIndex,
    CacheIndexEntry,
)
from claims_parser.mapping_models import WidgetMapping
from claims_parser.schema_models import FormSchema
from claims_parser.widget_models import Widget, WidgetCatalog

FINGERPRINT_VERSION = 1
RECT_BUCKET_PT = 1.0


def _bucket(v: float) -> float:
    return round(v / RECT_BUCKET_PT) * RECT_BUCKET_PT


def _widget_key(w: Widget) -> tuple:
    return (
        w.rect.page,
        w.field_name,
        w.on_value or "",
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
