# Mapping Cache for the Mapped AcroForm Branch — Design

**Date:** 2026-05-29
**Scope:** Add a fingerprint-keyed cache so the mapped AcroForm branch reuses a prior LLM-derived widget→label mapping when the same blank form is filled again for a different user.

## Goal

Today the mapped AcroForm branch runs the vision LLM (`map_widgets`) once per PDF. The output (`WidgetMapping` + `FormSchema` with `widget_bindings`) is deterministic with respect to the form's structure, not the user's data — so two users filling the same blank form pay the LLM cost twice for no reason.

This spec adds:

1. A **form fingerprint** computed from the widget catalog that is stable across PDF re-saves and xref renumbering.
2. A **cache** under `mappings/` (tracked in git) keyed by fingerprint that stores the `WidgetMapping` and `FormSchema`.
3. A **new orchestrator CLI** `map_widgets_cached.py` that checks the cache, falls back to the existing `map_widgets` on a miss, and rebinds `widget_xref` values to the current PDF on a hit.

Existing modules (`widget_extractor`, `widget_mapper`, `mapped_writer`, `form_filler`, all CLIs) do not change. The cache is purely additive.

## Non-goals

- No automatic invalidation policy. A new fingerprint = new cache entry; old entries stay until manually removed.
- No multi-machine or shared-volume cache. The cache is a git-tracked directory; multi-host reuse happens via the normal git workflow.
- No partial reuse. If even one widget can't be rebound, we fall through to a full LLM re-derivation.
- No change to the non-editable branch or the legacy AcroForm branch.

## Architecture

### Flow on cache miss (first time seeing a form)

```
PDF
  → extract_widget_catalog                      [existing]
  → compute_form_fingerprint(catalog)           [new]
  → cache.lookup(fingerprint) → None
  → map_widgets(pdf, catalog, di_context)       [existing, vision LLM + chunking]
  → cache.store(fingerprint, mapping, schema, source_name)
                                                  ├─ writes mappings/<fp>.mapping.json
                                                  └─ writes mappings/<fp>.schema.json
  → save_mapping / save_schema to output/intermediate/mapped/<stem>.*   [unchanged paths]
```

### Flow on cache hit (same form, different user)

```
PDF
  → extract_widget_catalog                      [existing]
  → compute_form_fingerprint(catalog)           [new]
  → cache.lookup(fingerprint) → CachedMapping
  → rebind_to_current_xrefs(mapping, schema, catalog) [new]
       ├─ for each binding: look up widget_field_name (+ on_value for radios)
       │  in the current catalog → overwrite widget_xref
       └─ if any binding's field_name is missing in current catalog → return None
  → if rebind succeeded:
       write output/intermediate/mapped/<stem>.{mapping,schema}.json from the rebound objects
       (no LLM call, no Azure call)
  → if rebind failed:
       log to stderr, fall through to the cache-miss path
```

### Why rebinding is necessary

`WidgetBinding.widget_xref` is the PDF object number used by [mapped_writer.py:144](../../../claims_parser/mapped_writer.py#L144) (`_find_widget(page, chosen.widget_xref)`) to locate the widget at write time. PDF re-saves renumber xrefs, so a cached xref from one PDF copy won't resolve in another.

`WidgetBinding.widget_field_name` is stable across copies (it's the AcroForm field path). Rebinding walks the cached bindings, looks each one up in the current catalog by `field_name` (plus `on_value` to disambiguate radio siblings), and overwrites `widget_xref`. Everything else in the binding (label, polygon, confidence, etc.) is unchanged.

### What stays unchanged

- `widget_extractor.py`, `widget_mapper.py`, `mapped_writer.py`, `form_filler.py`.
- `map_widgets.py`, `mapped_write.py`, `fill_schema.py`, `widget_extract.py` CLIs.
- `WidgetMapping`, `FormSchema`, `WidgetBinding`, `FilledFormSchema` pydantic models.
- Downstream consumers (`fill_schema.py`, `mapped_write.py`) never see the cache. They consume the same `<stem>.schema.json` and `<stem>.mapping.json` shapes as today.

## Fingerprint

### Input

Canonical JSON serialization of:

```json
{
  "v": 1,
  "page_count": <int>,
  "page_sizes_pt": [[w, h], ...],
  "widgets": [
    {
      "field_name": "...",
      "widget_type": "...",
      "page": <int>,
      "on_value": "..." | null,
      "choice_values": ["..."] | null,
      "rect": [x0, y0, x1, y1]
    },
    ...
  ]
}
```

### Normalization rules

- All coordinates (`page_sizes_pt`, `rect`) rounded to the nearest 0.5pt before hashing. Sub-point jitter from a re-save shouldn't break a cache hit; ≥0.5pt is enough movement to risk a different widget→label binding.
- Widgets sorted by `(page, field_name, on_value or "")` before serialization so catalog enumeration order can't affect the hash.
- `xref` is **not** in the input (it's not stable across PDF copies).
- `tu_label` is **not** in the input (it can drift across exporters even when the form is structurally identical, and the mapper doesn't rely on it for correctness).

### Output

SHA-256 hex digest (64 chars). Stored on `CachedMapping.form_fingerprint`.

### Stability claims

| Change | Same fingerprint? |
|---|---|
| Re-save with different PDF metadata/timestamps | yes |
| Xrefs renumbered | yes |
| Widget added / removed / renamed | no |
| `on_value` changed on any checkbox/radio | no |
| `choice_values` changed on any dropdown | no |
| Any widget moved by >0.5pt | no |
| Page size changed by >0.5pt | no |
| Page count changed | no |

## Cache layout

```
mappings/                             # tracked in git
  index.json                          # human-readable registry; optional, not required for lookups
  <fingerprint>.mapping.json          # WidgetMapping.model_dump_json()
  <fingerprint>.schema.json           # FormSchema.model_dump_json() (with widget_bindings baked in)
```

`index.json` records `{fingerprint → {source_pdf_basename, created_at}}`. It exists so a human can answer "which PDF created this cache entry?" without parsing every mapping file. The cache lookup itself uses the filename directly and does not depend on `index.json` being well-formed.

The mapping/schema files are identical in shape to what `map_widgets.py` already writes today, so `store()` is `Path(...).write_text(model.model_dump_json(indent=2))` and `lookup()` is `Model.model_validate_json(Path(...).read_text())`. No new wire format.

## Modules

### `claims_parser/mapping_cache_models.py` (new)

```python
class CachedMapping(BaseModel):
    form_fingerprint: str
    source_pdf_basename: str
    created_at: str            # ISO-8601 UTC
    mapping: WidgetMapping
    schema: FormSchema

class CacheIndexEntry(BaseModel):
    form_fingerprint: str
    source_pdf_basename: str
    created_at: str

class CacheIndex(BaseModel):
    entries: list[CacheIndexEntry]
```

### `claims_parser/mapping_cache.py` (new)

```python
def cache_dir() -> Path: ...                      # repo_root / "mappings"
def compute_form_fingerprint(catalog: WidgetCatalog) -> str: ...
def lookup(catalog: WidgetCatalog) -> Optional[CachedMapping]: ...
def store(
    fingerprint: str,
    mapping: WidgetMapping,
    schema: FormSchema,
    source_pdf_basename: str,
) -> None: ...
def rebind_to_current_xrefs(
    mapping: WidgetMapping,
    schema: FormSchema,
    catalog: WidgetCatalog,
) -> Optional[tuple[WidgetMapping, FormSchema]]: ...
```

`rebind_to_current_xrefs` returns `None` if any binding's `(field_name, on_value)` pair is absent in the current catalog. Caller treats `None` as a forced cache miss.

### `map_widgets_cached.py` (new root CLI)

```
usage: map_widgets_cached.py <pdf>
                             [--no-cache] [--rebuild] [--strict-cache]
                             [--mapping-out <path>] [--schema-out <path>]
```

Flag semantics:

- `--no-cache` — pure bypass: skip the cache read AND skip writing to it after derivation. Use when you don't want this run to affect cache state at all.
- `--rebuild` — force re-derivation and overwrite the cache entry under the same fingerprint. Useful after a prompt or model upgrade.
- `--strict-cache` — exit non-zero on a cache miss instead of falling through to the LLM. For CI/audit use.

`--no-cache` and `--rebuild` are mutually exclusive; passing both is an error.

Defaults for `--mapping-out` / `--schema-out` match `map_widgets.py` (under `output/intermediate/mapped/`).

stderr messages:

- `cache hit <fingerprint> (source: <basename>)`
- `cache miss, derived new mapping; stored under mappings/<fingerprint>.*`
- `cache hit but rebind failed (<n> bindings unresolvable); falling back to LLM`

### `claims_parser/__init__.py` additions

Re-export the cache surface. Module-internal names (`lookup`, `store`) are aliased at package level to avoid clashing with generic verbs:

```python
from claims_parser.mapping_cache import (
    compute_form_fingerprint,
    lookup as lookup_cached_mapping,
    store as store_cached_mapping,
    rebind_to_current_xrefs,
    cache_dir as mapping_cache_dir,
)
from claims_parser.mapping_cache_models import (
    CachedMapping, CacheIndex, CacheIndexEntry,
)
```

## Failure modes

| Situation | Behaviour |
|---|---|
| `mappings/` doesn't exist | created on first `store()`; `lookup()` returns `None` |
| `index.json` malformed | `store()` rewrites it; `lookup()` ignores it (uses file existence by fingerprint) |
| Cache file present but unreadable / fails schema validation | log to stderr, treat as miss, do not delete (operator decides) |
| Fingerprint matches but rebind fails | log to stderr with binding count, fall through to LLM, store fresh entry under the same fingerprint (overwrite) |
| `--strict-cache` + miss | exit 2 with a stderr error |

## Test plan

Manual integration tests using the two PDFs already in `input/`:

1. **Miss → store**
   `map_widgets_cached.py input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf` → stderr says `cache miss`, `mappings/<fp>.{mapping,schema}.json` appear, downstream `output/intermediate/mapped/ANTHEM*.{mapping,schema}.json` look identical to a fresh `map_widgets.py` run.

2. **Hit → reuse**
   Delete `output/intermediate/mapped/ANTHEM*`, re-run the same command → stderr says `cache hit`, no OpenAI call (verify via no network egress / `--no-vision`-equivalent log line), downstream files are byte-identical to step 1 modulo `widget_xref` (xrefs should also match if the same PDF; differ if a re-saved copy).

3. **Hit on a re-saved copy**
   `qpdf --linearize input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf input/anthem_resaved.pdf` (or any tool that renumbers xrefs), run cached CLI → still a hit, but the rebound `widget_xref` values differ from the original cache entry.

4. **Forced rebuild**
   `--rebuild` flag on ANTHEM → re-derives, overwrites cache, downstream files identical (modulo any LLM non-determinism, which should be ~zero given the prompt + temperature settings).

5. **CGS form**
   Same three steps for `56900_reopening.pdf` to confirm radio-group handling survives the round trip (catalog has 4 radio widgets; cached mapping must contain 4 bindings and rebind all 4 xrefs).

6. **Unit-level fingerprint stability** (in a one-off script, not committed as a test suite)
   - Same catalog twice → same hash.
   - Permute `widgets[]` order in the catalog → same hash.
   - Bump every rect by 0.3pt → same hash.
   - Bump one rect by 1pt → different hash.
   - Add a widget → different hash.

## Out of scope (future work)

- Automatic cache eviction by age or by mapper-prompt version.
- A `mappings/index.json` review UI / `--list-cache` flag.
- Cross-form similarity ("this is 90% like ANTHEM, want to reuse with manual diffs?"). Today it's a binary match or miss.
- Sharing the cache via S3/Azure Blob for multi-host fleets.
