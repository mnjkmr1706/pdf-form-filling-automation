# Mapping Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fingerprint-keyed cache to the mapped AcroForm branch so the same blank form is mapped by the vision LLM only once, regardless of how many users fill it.

**Architecture:** New `claims_parser/mapping_cache.py` + `mapping_cache_models.py` modules. A new root CLI `map_widgets_cached.py` orchestrates: extract widget catalog → compute fingerprint → cache lookup → on hit, rebind `widget_xref` to the current PDF and emit the same downstream files as `map_widgets.py`; on miss, run the existing `map_widgets` and store the result. The existing `map_widgets.py`, `mapped_writer.py`, `form_filler.py` and all CLIs are untouched.

**Tech Stack:** Python 3, pymupdf, pydantic v2, hashlib (stdlib SHA-256), argparse. No new third-party deps.

**Spec:** [docs/superpowers/specs/2026-05-29-mapping-cache-design.md](../specs/2026-05-29-mapping-cache-design.md)

---

## File map

| Path | Action | Responsibility |
|---|---|---|
| `claims_parser/mapping_cache_models.py` | create | `CachedMapping`, `CacheIndexEntry`, `CacheIndex` pydantic models |
| `claims_parser/mapping_cache.py` | create | `cache_dir`, `compute_form_fingerprint`, `lookup`, `store`, `rebind_to_current_xrefs` |
| `tests/__init__.py` | create | empty marker file |
| `tests/test_mapping_cache.py` | create | bare-assert unit tests for fingerprint + rebind (no pytest dep) |
| `map_widgets_cached.py` | create | root CLI orchestrator |
| `claims_parser/__init__.py` | modify | re-export cache surface |
| `mappings/` | create at runtime | git-tracked cache directory |

The project has no pytest installed, so tests use bare `assert` statements and are run via `python -m tests.test_mapping_cache`. This matches the existing repo style (no test suite today) while still giving us TDD discipline on pure functions.

---

## Task 1: Cache pydantic models

**Files:**
- Create: `claims_parser/mapping_cache_models.py`

- [ ] **Step 1: Write the module**

```python
"""Pydantic models for the mapping cache.

Stores a derived WidgetMapping + FormSchema keyed by a form fingerprint so
the vision-LLM step in the mapped AcroForm branch is paid once per blank
form, not once per filled copy.
"""
from pydantic import BaseModel

from claims_parser.mapping_models import WidgetMapping
from claims_parser.schema_models import FormSchema


class CachedMapping(BaseModel):
    form_fingerprint: str
    source_pdf_basename: str
    created_at: str  # ISO-8601 UTC
    mapping: WidgetMapping
    schema: FormSchema


class CacheIndexEntry(BaseModel):
    form_fingerprint: str
    source_pdf_basename: str
    created_at: str


class CacheIndex(BaseModel):
    entries: list[CacheIndexEntry] = []
```

- [ ] **Step 2: Verify it imports**

Run: `.venv-1/bin/python -c "from claims_parser.mapping_cache_models import CachedMapping, CacheIndex, CacheIndexEntry; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add claims_parser/mapping_cache_models.py
git commit -m "Add CachedMapping / CacheIndex pydantic models"
```

---

## Task 2: Fingerprint function (TDD)

**Files:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_mapping_cache.py`
- Create: `claims_parser/mapping_cache.py`

- [ ] **Step 1: Create the empty test package marker**

```python
# tests/__init__.py
```

- [ ] **Step 2: Write the failing fingerprint tests**

Create `tests/test_mapping_cache.py`:

```python
"""Bare-assert tests for the mapping cache. Run with:
    .venv-1/bin/python -m tests.test_mapping_cache
"""
from claims_parser.widget_models import Widget, WidgetCatalog, WidgetRect


def _w(name, t="text", page=1, rect=(10.0, 10.0, 100.0, 30.0), on_value=None, xref=1, choices=None):
    return Widget(
        field_name=name,
        widget_type=t,
        rect=WidgetRect(page=page, x0=rect[0], y0=rect[1], x1=rect[2], y1=rect[3]),
        xref=xref,
        tu_label=None,
        choice_values=choices,
        on_value=on_value,
    )


def _cat(widgets, page_count=1, sizes=((612.0, 792.0),)):
    return WidgetCatalog(
        file_name="x.pdf",
        page_count=page_count,
        page_sizes_pt=list(sizes),
        widgets=widgets,
    )


def test_fingerprint_is_deterministic():
    from claims_parser.mapping_cache import compute_form_fingerprint
    c = _cat([_w("a"), _w("b", t="checkbox", on_value="Yes")])
    assert compute_form_fingerprint(c) == compute_form_fingerprint(c)


def test_fingerprint_ignores_widget_order():
    from claims_parser.mapping_cache import compute_form_fingerprint
    a = _w("a", xref=1)
    b = _w("b", t="checkbox", on_value="Yes", xref=2)
    assert compute_form_fingerprint(_cat([a, b])) == compute_form_fingerprint(_cat([b, a]))


def test_fingerprint_ignores_xref():
    from claims_parser.mapping_cache import compute_form_fingerprint
    fp1 = compute_form_fingerprint(_cat([_w("a", xref=10), _w("b", xref=11)]))
    fp2 = compute_form_fingerprint(_cat([_w("a", xref=999), _w("b", xref=1000)]))
    assert fp1 == fp2


def test_fingerprint_ignores_tu_label():
    from claims_parser.mapping_cache import compute_form_fingerprint
    w1 = _w("a"); w1.tu_label = "Patient Name"
    w2 = _w("a"); w2.tu_label = "Member Name"
    assert compute_form_fingerprint(_cat([w1])) == compute_form_fingerprint(_cat([w2]))


def test_fingerprint_tolerates_subpoint_rect_jitter():
    from claims_parser.mapping_cache import compute_form_fingerprint
    a = _w("a", rect=(10.0, 10.0, 100.0, 30.0))
    b = _w("a", rect=(10.2, 10.1, 100.3, 30.4))
    assert compute_form_fingerprint(_cat([a])) == compute_form_fingerprint(_cat([b]))


def test_fingerprint_detects_rect_move_over_half_point():
    from claims_parser.mapping_cache import compute_form_fingerprint
    a = _w("a", rect=(10.0, 10.0, 100.0, 30.0))
    b = _w("a", rect=(12.0, 10.0, 102.0, 30.0))
    assert compute_form_fingerprint(_cat([a])) != compute_form_fingerprint(_cat([b]))


def test_fingerprint_detects_added_widget():
    from claims_parser.mapping_cache import compute_form_fingerprint
    one = _cat([_w("a")])
    two = _cat([_w("a"), _w("b")])
    assert compute_form_fingerprint(one) != compute_form_fingerprint(two)


def test_fingerprint_detects_renamed_field():
    from claims_parser.mapping_cache import compute_form_fingerprint
    assert compute_form_fingerprint(_cat([_w("a")])) != compute_form_fingerprint(_cat([_w("a_renamed")]))


def test_fingerprint_detects_on_value_change():
    from claims_parser.mapping_cache import compute_form_fingerprint
    a = _w("a", t="checkbox", on_value="Yes")
    b = _w("a", t="checkbox", on_value="On")
    assert compute_form_fingerprint(_cat([a])) != compute_form_fingerprint(_cat([b]))


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv-1/bin/python -m tests.test_mapping_cache`
Expected: every test ERRORs with `ImportError` / `ModuleNotFoundError` for `claims_parser.mapping_cache`.

- [ ] **Step 4: Implement `compute_form_fingerprint` in a new module**

Create `claims_parser/mapping_cache.py`:

```python
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
RECT_BUCKET_PT = 0.5


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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv-1/bin/python -m tests.test_mapping_cache`
Expected: all 9 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/__init__.py tests/test_mapping_cache.py claims_parser/mapping_cache.py
git commit -m "Add compute_form_fingerprint + tests"
```

---

## Task 3: `cache_dir`, `store`, `lookup` (round-trip)

**Files:**
- Modify: `claims_parser/mapping_cache.py`
- Modify: `tests/test_mapping_cache.py`

- [ ] **Step 1: Write the failing round-trip test**

Append to `tests/test_mapping_cache.py` (above the `if __name__ == "__main__":` block):

```python
def test_store_then_lookup_roundtrip(tmp_path_factory=None):
    import tempfile, os
    from claims_parser.mapping_cache import (
        compute_form_fingerprint, store, lookup, cache_dir,
    )
    from claims_parser.mapping_models import WidgetMapping
    from claims_parser.schema_models import FormSchema

    cat = _cat([_w("a"), _w("b", t="checkbox", on_value="Yes", xref=2)])
    fp = compute_form_fingerprint(cat)
    mapping = WidgetMapping(
        file_name="x.pdf", model="gpt-5-mini", chunk_count=1,
        bindings=[], unmapped_widget_field_names=[],
    )
    schema = FormSchema(form_title="x", sections=[], fields=[])

    with tempfile.TemporaryDirectory() as td:
        os.environ["PDF_PARSER_MAPPINGS_DIR"] = td
        try:
            assert lookup(cat) is None
            store(fp, mapping, schema, source_pdf_basename="x.pdf")
            cached = lookup(cat)
            assert cached is not None
            assert cached.form_fingerprint == fp
            assert cached.source_pdf_basename == "x.pdf"
            assert cached.mapping.file_name == "x.pdf"
            index_path = cache_dir() / "index.json"
            assert index_path.exists()
        finally:
            os.environ.pop("PDF_PARSER_MAPPINGS_DIR", None)
```

- [ ] **Step 2: Run tests to verify it fails**

Run: `.venv-1/bin/python -m tests.test_mapping_cache`
Expected: `test_store_then_lookup_roundtrip` ERRORs with `ImportError` for `store` / `lookup` / `cache_dir`.

- [ ] **Step 3: Add `cache_dir`, `store`, `lookup` to `claims_parser/mapping_cache.py`**

Append to `claims_parser/mapping_cache.py`:

```python
def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def cache_dir() -> Path:
    import os
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
        schema=schema,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-1/bin/python -m tests.test_mapping_cache`
Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add claims_parser/mapping_cache.py tests/test_mapping_cache.py
git commit -m "Add cache_dir / store / lookup with index registry"
```

---

## Task 4: `rebind_to_current_xrefs` (TDD)

**Files:**
- Modify: `claims_parser/mapping_cache.py`
- Modify: `tests/test_mapping_cache.py`

- [ ] **Step 1: Write the failing rebind tests**

Append to `tests/test_mapping_cache.py` (above the `if __name__ == "__main__":` block):

```python
def _binding(field_name, xref, on_value=None, semantic_id="x", page=1):
    from claims_parser.mapping_models import WidgetBinding
    return WidgetBinding(
        widget_field_name=field_name,
        widget_xref=xref,
        semantic_field_id=semantic_id,
        label_text="L",
        label_source="text_line",
        label_polygon=None,
        label_page=page,
        confidence=0.9,
        reasoning="r",
        option_label=None,
        on_value=on_value,
    )


def test_rebind_updates_xrefs_by_field_name():
    from claims_parser.mapping_cache import rebind_to_current_xrefs
    from claims_parser.mapping_models import WidgetMapping
    from claims_parser.schema_models import FormSchema, FormField

    cat = _cat([_w("a", xref=999), _w("b", xref=1000)])
    mapping = WidgetMapping(
        file_name="x.pdf", model="m", chunk_count=1,
        bindings=[_binding("a", xref=1), _binding("b", xref=2)],
        unmapped_widget_field_names=[],
    )
    schema = FormSchema(form_title="x", sections=[], fields=[
        FormField(
            field_id="x", label="L", section=None,
            field_type="text", source_text="L", required=False,
            format_hint=None, options=None,
            widget_bindings=[_binding("a", xref=1)],
        ),
        FormField(
            field_id="y", label="L2", section=None,
            field_type="text", source_text="L2", required=False,
            format_hint=None, options=None,
            widget_bindings=[_binding("b", xref=2)],
        ),
    ])

    rebound = rebind_to_current_xrefs(mapping, schema, cat)
    assert rebound is not None
    new_mapping, new_schema = rebound
    xrefs = {b.widget_field_name: b.widget_xref for b in new_mapping.bindings}
    assert xrefs == {"a": 999, "b": 1000}
    field_xrefs = {f.field_id: f.widget_bindings[0].widget_xref for f in new_schema.fields}
    assert field_xrefs == {"x": 999, "y": 1000}


def test_rebind_disambiguates_radio_siblings_by_on_value():
    from claims_parser.mapping_cache import rebind_to_current_xrefs
    from claims_parser.mapping_models import WidgetMapping
    from claims_parser.schema_models import FormSchema, FormField

    cat = _cat([
        _w("radio1", t="radio", on_value="Yes", xref=501),
        _w("radio1", t="radio", on_value="No", xref=502),
    ])
    mapping = WidgetMapping(
        file_name="x.pdf", model="m", chunk_count=1,
        bindings=[
            _binding("radio1", xref=1, on_value="Yes"),
            _binding("radio1", xref=2, on_value="No"),
        ],
        unmapped_widget_field_names=[],
    )
    schema = FormSchema(form_title="x", sections=[], fields=[
        FormField(
            field_id="q", label="L", section=None,
            field_type="radio_group", source_text="L", required=False,
            format_hint=None, options=["Yes", "No"],
            widget_bindings=[
                _binding("radio1", xref=1, on_value="Yes"),
                _binding("radio1", xref=2, on_value="No"),
            ],
        ),
    ])
    rebound = rebind_to_current_xrefs(mapping, schema, cat)
    assert rebound is not None
    new_mapping, _ = rebound
    by_on = {b.on_value: b.widget_xref for b in new_mapping.bindings}
    assert by_on == {"Yes": 501, "No": 502}


def test_rebind_returns_none_when_field_missing():
    from claims_parser.mapping_cache import rebind_to_current_xrefs
    from claims_parser.mapping_models import WidgetMapping
    from claims_parser.schema_models import FormSchema

    cat = _cat([_w("a", xref=999)])
    mapping = WidgetMapping(
        file_name="x.pdf", model="m", chunk_count=1,
        bindings=[_binding("a", xref=1), _binding("missing", xref=2)],
        unmapped_widget_field_names=[],
    )
    schema = FormSchema(form_title="x", sections=[], fields=[])
    assert rebind_to_current_xrefs(mapping, schema, cat) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-1/bin/python -m tests.test_mapping_cache`
Expected: the three new tests ERROR with `ImportError` for `rebind_to_current_xrefs`.

- [ ] **Step 3: Implement `rebind_to_current_xrefs`**

Append to `claims_parser/mapping_cache.py`:

```python
def _build_xref_lookup(catalog: WidgetCatalog) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for w in catalog.widgets:
        out[(w.field_name, w.on_value or "")] = w.xref
    return out


def _rebind_one(binding, lookup_table: dict[tuple[str, str], int]) -> Optional[int]:
    key_specific = (binding.widget_field_name, binding.on_value or "")
    if key_specific in lookup_table:
        return lookup_table[key_specific]
    # Fall back: field without on_value (text / signature / single checkbox)
    key_unspecific = (binding.widget_field_name, "")
    if key_unspecific in lookup_table:
        return lookup_table[key_unspecific]
    return None


def rebind_to_current_xrefs(
    mapping: WidgetMapping,
    schema: FormSchema,
    catalog: WidgetCatalog,
):
    table = _build_xref_lookup(catalog)
    new_bindings = []
    for b in mapping.bindings:
        new_xref = _rebind_one(b, table)
        if new_xref is None:
            return None
        new_bindings.append(b.model_copy(update={"widget_xref": new_xref}))
    new_mapping = mapping.model_copy(update={"bindings": new_bindings})

    new_fields = []
    for f in schema.fields:
        if not f.widget_bindings:
            new_fields.append(f)
            continue
        rebound_field_bindings = []
        for b in f.widget_bindings:
            new_xref = _rebind_one(b, table)
            if new_xref is None:
                return None
            rebound_field_bindings.append(b.model_copy(update={"widget_xref": new_xref}))
        new_fields.append(f.model_copy(update={"widget_bindings": rebound_field_bindings}))
    new_schema = schema.model_copy(update={"fields": new_fields})

    return new_mapping, new_schema
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-1/bin/python -m tests.test_mapping_cache`
Expected: all 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add claims_parser/mapping_cache.py tests/test_mapping_cache.py
git commit -m "Add rebind_to_current_xrefs with radio on_value disambiguation"
```

---

## Task 5: Re-export from `claims_parser/__init__.py`

**Files:**
- Modify: `claims_parser/__init__.py`

- [ ] **Step 1: Add imports below the existing `write_mapped` import**

Insert immediately after the line `from claims_parser.mapped_writer import write_mapped`:

```python
from claims_parser.mapping_cache import (
    compute_form_fingerprint,
    lookup as lookup_cached_mapping,
    store as store_cached_mapping,
    rebind_to_current_xrefs,
    cache_dir as mapping_cache_dir,
)
from claims_parser.mapping_cache_models import (
    CachedMapping,
    CacheIndex,
    CacheIndexEntry,
)
```

- [ ] **Step 2: Extend `__all__`**

In `claims_parser/__init__.py`, locate the closing `]` of the `__all__` list and insert these entries just before it (after `"write_mapped",`):

```python
    # mapping cache
    "compute_form_fingerprint",
    "lookup_cached_mapping",
    "store_cached_mapping",
    "rebind_to_current_xrefs",
    "mapping_cache_dir",
    "CachedMapping",
    "CacheIndex",
    "CacheIndexEntry",
```

- [ ] **Step 3: Verify package imports cleanly**

Run: `.venv-1/bin/python -c "from claims_parser import compute_form_fingerprint, lookup_cached_mapping, store_cached_mapping, rebind_to_current_xrefs, mapping_cache_dir, CachedMapping; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add claims_parser/__init__.py
git commit -m "Re-export mapping cache surface from claims_parser package"
```

---

## Task 6: `map_widgets_cached.py` CLI

**Files:**
- Create: `map_widgets_cached.py`

The mapped branch does NOT use Azure (the existing `map_widgets.py` requires a context.json only because `map_widgets()` reads `DocumentContext` for KVP hints — see [widget_mapper.py](claims_parser/widget_mapper.py)). On a cache hit we skip both the LLM and the context-loading step. On a miss we must call `map_widgets` with a real context, so the CLI requires the context argument the same way `map_widgets.py` does.

- [ ] **Step 1: Write the CLI**

Create `map_widgets_cached.py`:

```python
"""CLI: map_widgets with a fingerprint-keyed cache.

On a cache hit, no LLM call is made: the cached WidgetMapping + FormSchema
are rebound to the current PDF's widget xrefs and written to the same
output paths as map_widgets.py.

Usage:
    python map_widgets_cached.py input/<form>.pdf \\
        output/intermediate/<form>.context.json \\
        output/intermediate/mapped/<form>.widgets.json
"""
import argparse
import sys
from pathlib import Path

from claims_parser.extractor import load_context
from claims_parser.mapping_cache import (
    compute_form_fingerprint,
    lookup,
    rebind_to_current_xrefs,
    store,
)
from claims_parser.schema_builder import save_schema
from claims_parser.widget_extractor import load_catalog
from claims_parser.widget_mapper import (
    DEFAULT_MODEL,
    build_schema_from_mapping,
    map_widgets,
    save_mapping,
)

OUT_DIR = Path("output/intermediate/mapped")


def main() -> int:
    p = argparse.ArgumentParser(description="Mapped AcroForm branch with mapping cache.")
    p.add_argument("pdf")
    p.add_argument("context", help="DocumentContext JSON produced by extract.py.")
    p.add_argument("widgets", help="WidgetCatalog JSON produced by widget_extract.py.")
    p.add_argument("-m", "--model", default=DEFAULT_MODEL)
    p.add_argument("--mapping-out", default=None)
    p.add_argument("--schema-out", default=None)
    p.add_argument("--no-cache", action="store_true",
                   help="Bypass cache entirely: skip read AND skip write.")
    p.add_argument("--rebuild", action="store_true",
                   help="Force re-derivation and overwrite the cache entry.")
    p.add_argument("--strict-cache", action="store_true",
                   help="Exit non-zero on cache miss instead of running the LLM.")
    args = p.parse_args()

    if args.no_cache and args.rebuild:
        print("✗ --no-cache and --rebuild are mutually exclusive", file=sys.stderr)
        return 2

    pdf_path = Path(args.pdf)
    ctx_path = Path(args.context)
    cat_path = Path(args.widgets)
    for p_ in (pdf_path, cat_path):
        if not p_.exists():
            print(f"✗ Not found: {p_}", file=sys.stderr)
            return 1

    stem = pdf_path.stem
    mapping_out = Path(args.mapping_out) if args.mapping_out else OUT_DIR / f"{stem}.mapping.json"
    schema_out = Path(args.schema_out) if args.schema_out else OUT_DIR / f"{stem}.schema.json"

    print(f"→ Loading widgets: {cat_path.name}")
    catalog = load_catalog(cat_path)
    print(f"  {len(catalog.widgets)} widgets over {catalog.page_count} page(s)")

    fingerprint = compute_form_fingerprint(catalog)
    print(f"→ Form fingerprint: {fingerprint}")

    use_cache_read = not (args.no_cache or args.rebuild)
    cached = lookup(catalog) if use_cache_read else None

    if cached is not None:
        rebound = rebind_to_current_xrefs(cached.mapping, cached.schema, catalog)
        if rebound is not None:
            mapping, schema = rebound
            mapping_out.parent.mkdir(parents=True, exist_ok=True)
            schema_out.parent.mkdir(parents=True, exist_ok=True)
            save_mapping(mapping, mapping_out)
            save_schema(schema, schema_out)
            print(f"✓ Cache hit (source: {cached.source_pdf_basename}); "
                  f"no LLM call. mapping={mapping_out} schema={schema_out}")
            return 0
        else:
            print("! Cache hit but rebind failed; falling back to LLM",
                  file=sys.stderr)

    if args.strict_cache:
        print("✗ Cache miss with --strict-cache", file=sys.stderr)
        return 2

    if not ctx_path.exists():
        print(f"✗ Not found: {ctx_path}", file=sys.stderr)
        return 1
    print(f"→ Loading context: {ctx_path.name}")
    doc_ctx = load_context(ctx_path)
    print(f"  {len(doc_ctx.lines)} lines, {len(doc_ctx.key_value_pairs)} KVPs, "
          f"{len(doc_ctx.tables)} tables, {len(doc_ctx.selection_marks)} marks")

    print(f"→ Mapping with {args.model} (vision)...")
    mapping = map_widgets(pdf_path, doc_ctx, catalog, model=args.model)
    schema = build_schema_from_mapping(catalog, mapping)
    save_mapping(mapping, mapping_out)
    save_schema(schema, schema_out)
    print(f"✓ Derived fresh mapping. mapping={mapping_out} schema={schema_out}")
    print(f"  bindings: {len(mapping.bindings)}, unmapped: {len(mapping.unmapped_widget_field_names)}, "
          f"chunks: {mapping.chunk_count}")

    if args.no_cache:
        print("→ --no-cache set; skipping cache write")
    else:
        store(fingerprint, mapping, schema, source_pdf_basename=pdf_path.name)
        print(f"✓ Stored cache entry: mappings/{fingerprint}.*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Sanity-check `--help` works**

Run: `.venv-1/bin/python map_widgets_cached.py --help`
Expected: argparse usage output listing all three positional args and the four optional flags.

- [ ] **Step 3: Commit**

```bash
git add map_widgets_cached.py
git commit -m "Add map_widgets_cached.py CLI with cache hit / miss / strict / rebuild paths"
```

---

## Task 7: Integration test — cache miss path on ANTHEM

This uses the already-cached `output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.context.json` and `output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.widgets.json` that were produced during earlier runs (still on disk per CLAUDE.md and the previous session).

- [ ] **Step 1: Verify no cache entry exists yet**

Run: `ls mappings/ 2>/dev/null || echo "mappings/ does not exist (expected)"`
Expected: either no output or the directory does not exist.

- [ ] **Step 2: Snapshot the existing schema for comparison**

Run:
```bash
cp output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.schema.json /tmp/anthem.before.schema.json
cp output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.mapping.json /tmp/anthem.before.mapping.json
```

- [ ] **Step 3: Run the cached CLI on ANTHEM (cache miss)**

Run:
```bash
.venv-1/bin/python map_widgets_cached.py \
  input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf \
  output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.context.json \
  output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.widgets.json
```

Expected stdout includes `→ Form fingerprint: <64-hex>`, `✓ Derived fresh mapping.`, `✓ Stored cache entry: mappings/<fp>.*`.

- [ ] **Step 4: Verify cache files were written**

Run: `ls mappings/`
Expected: `<fingerprint>.mapping.json`, `<fingerprint>.schema.json`, `<fingerprint>.cached.json`, `index.json` all present.

- [ ] **Step 5: Confirm downstream files still drive fill_schema + mapped_write**

Run:
```bash
.venv-1/bin/python fill_schema.py output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.schema.json
.venv-1/bin/python mapped_write.py input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf \
    output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.filled.json
```

Expected: both succeed; `output/final/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.filled.pdf` is produced; review report has zero items or only items that also appeared in the prior session.

- [ ] **Step 6: Commit**

```bash
git add mappings/
git commit -m "Cache ANTHEM mapping (integration test artifact)"
```

---

## Task 8: Integration test — cache hit path on ANTHEM

- [ ] **Step 1: Delete the downstream artifacts**

Run:
```bash
rm output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.schema.json
rm output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.mapping.json
```

- [ ] **Step 2: Re-run the cached CLI (expect a hit, no LLM)**

Run:
```bash
time .venv-1/bin/python map_widgets_cached.py \
  input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf \
  output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.context.json \
  output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.widgets.json
```

Expected stdout: `✓ Cache hit (source: ANTHEM_NV_CAID_ClaimsAppealsForm.pdf); no LLM call.`
Expected wall-clock: well under a second (no `Mapping with gpt-5-mini` line should appear).

- [ ] **Step 3: Verify schemas match (modulo nothing — should be byte-identical)**

Run:
```bash
diff /tmp/anthem.before.schema.json output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.schema.json
diff /tmp/anthem.before.mapping.json output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.mapping.json
```

Expected: no diff output (cache hit on the same PDF reproduces the same xrefs because the catalog is identical).

- [ ] **Step 4: Run the full downstream pipeline against the cached schema**

Run:
```bash
.venv-1/bin/python fill_schema.py output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.schema.json
.venv-1/bin/python mapped_write.py input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf \
    output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.filled.json
```

Expected: filled PDF produced successfully, review report has zero items beyond what step Task 7.5 produced.

---

## Task 9: Integration test — radio-group round trip on CGS

- [ ] **Step 1: Run cached CLI on CGS (cache miss, will derive)**

Run:
```bash
.venv-1/bin/python map_widgets_cached.py \
  input/56900_reopening.pdf \
  output/intermediate/56900_reopening.context.json \
  output/intermediate/mapped/56900_reopening.widgets.json
```

Expected: `✓ Derived fresh mapping.` followed by `✓ Stored cache entry`.

- [ ] **Step 2: Verify radio bindings are present in the cached mapping**

Run:
```bash
.venv-1/bin/python -c "
import json, glob
for p in glob.glob('mappings/*.cached.json'):
    d = json.loads(open(p).read())
    if 'reopening' in d['source_pdf_basename'].lower():
        radios = [b for b in d['mapping']['bindings'] if b['on_value']]
        print(d['source_pdf_basename'], len(radios), 'radio/checkbox bindings with on_value')
        for b in radios:
            print(' ', b['widget_field_name'], '/', b['on_value'])
"
```

Expected: at least 4 bindings printed with `on_value` populated.

- [ ] **Step 3: Cache-hit re-run with downstream artifacts deleted**

Run:
```bash
rm output/intermediate/mapped/56900_reopening.schema.json
rm output/intermediate/mapped/56900_reopening.mapping.json
.venv-1/bin/python map_widgets_cached.py \
  input/56900_reopening.pdf \
  output/intermediate/56900_reopening.context.json \
  output/intermediate/mapped/56900_reopening.widgets.json
```

Expected: `✓ Cache hit`.

- [ ] **Step 4: Fill + write end-to-end and confirm a radio renders**

Run:
```bash
.venv-1/bin/python fill_schema.py output/intermediate/mapped/56900_reopening.schema.json
.venv-1/bin/python mapped_write.py input/56900_reopening.pdf \
    output/intermediate/mapped/56900_reopening.filled.json
```

Expected: `output/final/mapped/56900_reopening.filled.pdf` produced; review report has zero `unsettable_widget` items.

- [ ] **Step 5: Commit cache addition**

```bash
git add mappings/
git commit -m "Cache CGS 56900_reopening mapping (integration test artifact)"
```

---

## Task 10: Flag verification

- [ ] **Step 1: `--strict-cache` should succeed on a hit**

Run:
```bash
.venv-1/bin/python map_widgets_cached.py \
  input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf \
  output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.context.json \
  output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.widgets.json \
  --strict-cache
echo "exit=$?"
```

Expected: `✓ Cache hit ...`, `exit=0`.

- [ ] **Step 2: `--strict-cache` should fail on a synthetic miss**

Force a miss by pointing the CLI at an env-overridden empty cache dir:

```bash
PDF_PARSER_MAPPINGS_DIR=/tmp/empty-cache .venv-1/bin/python map_widgets_cached.py \
  input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf \
  output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.context.json \
  output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.widgets.json \
  --strict-cache
echo "exit=$?"
```

Expected: `✗ Cache miss with --strict-cache`, `exit=2`.

- [ ] **Step 3: `--no-cache` + `--rebuild` should error**

Run:
```bash
.venv-1/bin/python map_widgets_cached.py \
  input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf \
  output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.context.json \
  output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.widgets.json \
  --no-cache --rebuild
echo "exit=$?"
```

Expected: `✗ --no-cache and --rebuild are mutually exclusive`, `exit=2`.

---

## Task 11: Documentation pointer in CLAUDE.md

Add a one-line pointer so future Claude sessions discover the cache without reading the spec.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a line under the AcroForm commands section**

Locate the `# AcroForm branch (editable PDFs)` block in `CLAUDE.md` and append after the existing `acroform_write.py` invocation:

```bash
# Mapped AcroForm branch with cache (skip vision LLM on repeat forms)
.venv-1/bin/python map_widgets_cached.py input/<name>.pdf \
                                         output/intermediate/<name>.context.json \
                                         output/intermediate/mapped/<name>.widgets.json
```

(If a "Mapped AcroForm branch" section already exists, insert under it instead.)

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Document map_widgets_cached.py invocation in CLAUDE.md"
```

---

## Final verification checklist

After all tasks are done, run the consolidated check:

- [ ] `.venv-1/bin/python -m tests.test_mapping_cache` → all tests PASS
- [ ] `ls mappings/` → at least 2 fingerprints + index.json
- [ ] Cold cache hit on ANTHEM completes in <1s
- [ ] Cold cache hit on CGS produces a filled PDF whose radio button visibly renders
- [ ] `git status` → clean
