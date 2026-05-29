"""Form fingerprint for the mapped AcroForm branch.

A stable hash of the WidgetCatalog used to key the mapping cache. Independent
of xref renumbering and sub-point rect jitter.
"""
from __future__ import annotations

import hashlib
import json
import os
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def cache_dir() -> Path:
    override = os.environ.get("PDF_PARSER_MAPPINGS_DIR")
    if override:
        return Path(override)
    return _repo_root() / "mappings"


def _mapping_path(fingerprint: str) -> Path:
    return cache_dir() / f"{fingerprint}.mapping.json"


def _schema_path(fingerprint: str) -> Path:
    return cache_dir() / f"{fingerprint}.schema.json"


def _cached_path(fingerprint: str) -> Path:
    return cache_dir() / f"{fingerprint}.cached.json"


def _index_path() -> Path:
    return cache_dir() / "index.json"


def _load_index() -> CacheIndex:
    p = _index_path()
    if not p.exists():
        return CacheIndex(entries=[])
    try:
        return CacheIndex.model_validate_json(p.read_text())
    except Exception:
        return CacheIndex(entries=[])


def _write_index(index: CacheIndex) -> None:
    _index_path().write_text(index.model_dump_json(indent=2))


def store(
    fingerprint: str,
    mapping: WidgetMapping,
    schema: FormSchema,
    source_pdf_basename: str,
) -> None:
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    cached = CachedMapping(
        form_fingerprint=fingerprint,
        source_pdf_basename=source_pdf_basename,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        mapping=mapping,
        form_schema=schema,
    )
    _cached_path(fingerprint).write_text(cached.model_dump_json(indent=2))
    _mapping_path(fingerprint).write_text(mapping.model_dump_json(indent=2))
    _schema_path(fingerprint).write_text(schema.model_dump_json(indent=2))

    index = _load_index()
    index.entries = [e for e in index.entries if e.form_fingerprint != fingerprint]
    index.entries.append(CacheIndexEntry(
        form_fingerprint=fingerprint,
        source_pdf_basename=source_pdf_basename,
        created_at=cached.created_at,
    ))
    _write_index(index)


def lookup(catalog: WidgetCatalog) -> Optional[CachedMapping]:
    fingerprint = compute_form_fingerprint(catalog)
    p = _cached_path(fingerprint)
    if not p.exists():
        return None
    try:
        return CachedMapping.model_validate_json(p.read_text())
    except Exception:
        return None
