# Database-driven Fill — Design Reference

**Status:** Analysis only. No code changes yet. Use this as the reference when the database JSON is provided and code work begins.

**Goal:** Replace Agent 2 (LLM filler) with a deterministic mapper that takes the existing `FormSchema` plus a database JSON and produces the same `<stem>.filled.json` artifact the downstream writers already consume.

The change is localized — all three branches converge on the same filled-schema shape, so the writers (non-editable, AcroForm, mapped AcroForm) need **no behavioral change** if the new filler preserves the contract.

---

## 1. What Agent 2 produces today (the contract to preserve)

[claims_parser/filler_models.py](../claims_parser/filler_models.py): `FilledFormSchema` extends `FormSchema` and adds:

```python
class FilledFormField(FormField):
    value: Union[str, list[str], None]
```

Per-`field_type` rules the writers rely on:

| field_type | value shape |
| --- | --- |
| `text`, `long_text`, `date`, `time`, `phone`, `email`, `address`, `number`, `currency`, `identifier`, `signature` | single `str` |
| `radio_group` | exactly one `str` drawn from `options` |
| `checkbox` | `list[str]`, zero-or-more drawn from `options` |
| (any) | `None` allowed but produces `missing_value` in review |

Also preserved: `widget_bindings` on each `FilledFormField` (only set by the mapped-AcroForm branch — passed through unchanged today; the new filler must do the same).

Any replacement filler must produce this exact shape. The CLI ([fill_schema.py](../fill_schema.py)) writes `output/intermediate/<stem>.filled.json` — this path is the integration point with all three writers.

---

## 2. The mapping problem (the only hard part)

`FormField.field_id` and `FormField.label` are derived **per form** from OCR/widget text. They vary across insurers: one form's "Member ID" is another's "Subscriber Number" is another's "Patient ID Number". The database JSON, presumably, has its own canonical keys — stable across forms.

So the work splits in two:

1. **Schema-to-DB key resolution** (form-dependent, non-trivial): for each `FormField`, find which DB key supplies its value.
2. **Value materialization** (form-independent, mechanical): take the DB value, coerce to the `field_type` shape (format date, pick a `radio_group` option, etc.), produce `FilledFormField.value`.

Step 2 is purely deterministic. Step 1 is where design decisions matter.

### Three strategies for step 1

| | (a) Manual mapping file | (b) LLM-assisted, cached | (c) Heuristic match |
| --- | --- | --- | --- |
| **How** | Hand-author `field_id → db_key` per form | One-time LLM call matches schema labels to DB keys; persist to disk | Token / fuzzy / embedding match against DB key labels |
| **Per-form effort** | High (every new form) | Low (LLM does it once, human reviews) | None |
| **Accuracy** | Highest | High after review | Medium — fails on terse / ambiguous labels |
| **First-run cost** | Manual time | 1 LLM call/form | 0 |
| **Recurring cost** | 0 | 0 (cached) | 0 |
| **Domain-genericness** | Preserved | Preserved | Preserved |

**Recommended approach: (b) with (c) as a fallback.**

Mirrors the existing mapping-cache pattern for widget→label binding ([docs/superpowers/specs/2026-05-29-mapping-cache-design.md](superpowers/specs/2026-05-29-mapping-cache-design.md)): expensive resolution once, hash-keyed cache, fast deterministic reuse.

---

## 3. Proposed architecture

```
input/<name>.pdf ──► [Agent 1 OR AcroForm extractor OR mapped extractor]
                     produces FormSchema (unchanged)

intermediate/<name>.schema.json + database/<case>.json
        │
        ▼
[NEW: db_mapper.py] ──► intermediate/<name>.fillmap.json    (schema → db_key map; cacheable)
        │
        ▼
[NEW: db_filler.py] ──► intermediate/<name>.filled.json     (same shape as today)
        │
        ▼
existing writers (no change)
```

Two new modules, intentionally separated so the *resolution* step (LLM, slow, cacheable) is independent of the *materialization* step (deterministic, fast, no network).

### 3.1 `db_mapper.py` — schema → DB key resolver

- Input: `FormSchema`, `dict` of DB JSON (or just its top-level key list + sample values).
- Output: `FieldMapping` artifact — for each `FormField.field_id`:
  - `db_key: str | None` (the key in DB JSON, or `None` if unmatched)
  - `confidence: Literal["high", "medium", "low", "unmatched"]`
  - For choice fields: `option_mapping: dict[str, str]` (the form's option label → the DB value that should select it, or vice-versa)
  - `reason: str` (one line: why this match, or why unmatched)
- Resolution cascade:
  1. **Exact key match** — `field_id == db_key` (snake-case label collision; cheap win)
  2. **Heuristic / fuzzy** — token-overlap, embedding similarity over `label`/`section` vs DB key + DB-key-doc (if available)
  3. **LLM** — send `(field_id, label, section, field_type, options)` plus the DB key list with sample values; ask for the best matching key per field, allowing `null`. Use structured output (Pydantic model) to keep this reproducible.
- Cache: hash `FormSchema.fields[*].(field_id,label,section,field_type,options)` + the DB schema (key set, not values) → file at `output/intermediate/<stem>.fillmap.json`. Skip the LLM if the cache key matches.

### 3.2 `db_filler.py` — DB value → filled schema

Pure, deterministic. No LLM, no network. Takes (`FormSchema`, `FieldMapping`, `db_json: dict`) → `FilledFormSchema`.

For each `FormField`:

1. Look up `db_key` in the `FieldMapping`. If `None` / unmatched → record `value=None`, flag for review.
2. Pull the raw DB value.
3. Coerce by `field_type`:
   - `text` / `long_text` / `signature` / `address` → `str(value)` (or join lines for `address`)
   - `date` → format per `format_hint` if present, else ISO `YYYY-MM-DD`
   - `time` → format per `format_hint` else `HH:MM`
   - `phone` → digits only or per `format_hint` (e.g., `(NNN) NNN-NNNN`)
   - `number` / `currency` → numeric → string per `format_hint`
   - `email`, `identifier` → `str(value)`
   - `radio_group` → resolve DB value to one of `options` using `option_mapping`; if no match, leave `None` and flag
   - `checkbox` → DB value is `list` (or comma-separated string) → resolve each to `options`, drop unmatched, flag any
4. Preserve `widget_bindings` (mapped-AcroForm branch).

All these coercions live in one module so behavior is grep-able. None of them encode form-specific assumptions.

### 3.3 CLI

Two options:

- **(i)** Replace `fill_schema.py` behavior conditionally: add a `--db-json PATH` flag. When present, run mapper+filler; when absent, fall back to the LLM filler. **Pro:** zero call-site churn for the three workflows. **Con:** two execution paths inside one CLI.
- **(ii)** New CLI `map_db.py` (schema + db → fillmap) and reuse `fill_schema.py` with a `--fillmap PATH` flag. **Pro:** clear separation; each artifact is its own step. **Con:** one more command in the recipe.

Recommend **(i)** for ergonomics — the user-facing recipe stays a 4-step pipeline. Detect the new mode from flag presence:

```bash
.venv-1/bin/python fill_schema.py output/intermediate/X.schema.json \
                                   --db-json database/case-12345.json
```

---

## 4. Per-branch impact

All three branches share `fill_schema.py`, so the change is in one place. The branch-specific differences are limited to **what's in `FormSchema` already**.

### 4.1 Non-editable (scanned) branch
- `FormField.widget_bindings` is `None`. Filler just produces `value`.
- Writer ([pdf_writer.py](../claims_parser/pdf_writer.py)) anchors against `source_text` and stamps. **No change.**
- `source_text` must remain verbatim; do not let the mapper or filler mutate it.

### 4.2 AcroForm (direct) branch
- `FormField.widget_bindings` is `None` (this branch uses a separate `AcroFormBinding` artifact tied to `field_id`).
- Filler produces `value`. Writer ([acroform_writer.py](../claims_parser/acroform_writer.py)) reads `binding.json` and sets widget values. **No change.**
- For radio groups, `value` must match an `option_id` / `on_value` the binding expects — same constraint as today's LLM. The option resolver in `db_filler.py` is the place to enforce this.

### 4.3 Mapped AcroForm branch
- `FormField.widget_bindings` is **populated**. Filler must preserve it (today's `form_filler.py` re-attaches bindings after the LLM call; the new filler can attach them directly since it never strips them).
- Writer ([mapped_writer.py](../claims_parser/mapped_writer.py)) consumes the bindings inline. **No change.**

### 4.4 Shared: the review report
- The writers' `<stem>.review.json` already includes `missing_value` as a reason. Unmapped DB fields fall naturally into this bucket — no new reason code strictly required.
- Optional addition: a new reason `unmapped_db_key` to distinguish "we had no data" from "LLM gave up." Easy to add to [claims_parser/review_models.py](../claims_parser/review_models.py).

---

## 5. Edge cases the design must answer

These are not implementation details — they're product decisions the user should resolve once the DB JSON shape is known.

1. **Unmapped schema fields.** A form asks for a field the DB JSON does not carry. Two choices: (a) leave `value=None`, surface in review; (b) fall back to LLM for those fields only (hybrid). Default proposal: **(a)** — deterministic is the point.
2. **Conditional sub-questions.** Today Agent 2 fills every "If yes, date:" field unconditionally. With real data, conditionals matter. Default proposal: respect the conditional — if the gating field's DB value is "No", leave dependents `None`. But this requires the schema to encode dependency, which it does not today. **Open question.** Simplest interim: still fill everything the DB has; let the writer stamp it.
3. **Option matching for `radio_group` / `checkbox`.** DB has "Medical" but the form option is "Medical Plan". Need a per-field option mapping (built once by the mapper, cached). Boolean DB values (`true`/`false`) → match yes/no-style options via case-insensitive substring.
4. **Format mismatches.** DB has `1980-04-23` but the form's `format_hint` is `MM/DD/YYYY`. Coercion must reformat; never pass a DB date through verbatim.
5. **Multi-value DB fields for single-value form fields.** DB has a `phone_numbers: [...]` list but the form asks for one phone. Pick first; flag in review.
6. **Empty / null DB value.** Treat as `unmapped_db_key` (no data) — do not invent.
7. **Address composition.** DB might split address into `street/city/state/zip`; the form might have one box or multiple. Either join on render or rely on the mapper to map each address-component field to the right DB sub-key. The `field_type=address` case should pick a sensible default join when the form has one box.
8. **Signatures.** DB unlikely to carry a signature. Likely always `None`; surface in review.

---

## 6. Files to add / modify (when implementation begins)

**New:**
- `claims_parser/db_mapping_models.py` — `FieldMapping`, `FieldMatch`, `OptionMatch` Pydantic models.
- `claims_parser/db_mapper.py` — schema + DB → `FieldMapping` (LLM + heuristic cascade, cache).
- `claims_parser/db_filler.py` — `FormSchema` + `FieldMapping` + DB → `FilledFormSchema` (pure, deterministic).

**Modified:**
- [claims_parser/__init__.py](../claims_parser/__init__.py) — export the two new functions.
- [fill_schema.py](../fill_schema.py) — add `--db-json PATH` flag; when set, take the new path and skip the LLM filler.
- [claims_parser/review_models.py](../claims_parser/review_models.py) — optional new reason `unmapped_db_key`.

**Untouched:**
- All extractors, anchor placer, vision passes, writers. The contract at `<stem>.filled.json` is what protects them.
- `claims_parser/form_filler.py` stays as-is so the LLM path remains available (useful for forms with no DB data, or for testing the writer in isolation).

---

## 7. Open questions for when the DB JSON arrives

These determine the mapper's surface area; answer them up front:

1. **Shape of the DB JSON.** Flat object `{key: value}`, or nested (e.g., `{"member": {...}, "claim": {...}}`)? Nested is fine but the mapper needs a flattening convention (dotted keys).
2. **Is there a documented canonical key set**, or is the DB JSON the only source of truth for what keys exist? Documented keys let the mapper short-circuit some LLM work.
3. **One DB JSON per form, or one DB JSON per case (reused across forms)?** Affects whether the `FieldMapping` cache is per-form (good) or per-(form, case) tuple (bad — defeats caching).
4. **Hybrid LLM fallback** for unmapped fields: desired or not? (Default proposal: no.)
5. **Conditional honoring** (§5.2): yes or no?
6. **Where do DB JSONs live in the repo?** Proposed `database/` under repo root, gitignored (treat like `input/` for PII).

---

## 8. Suggested execution order

When code work starts:

1. Add the Pydantic models for `FieldMapping`. No logic yet — get the artifact shape right.
2. Implement `db_filler.py` first, end-to-end, using a **hand-written** `FieldMapping` against a real DB JSON and one real form. This proves the materialization rules independently of the mapper.
3. Verify the writers produce a correct filled PDF using that hand-written mapping. Zero changes to writers should be needed; if they are, the contract analysis above is wrong.
4. Implement `db_mapper.py` (heuristic + LLM + cache).
5. Wire the `--db-json` flag into `fill_schema.py`.
6. Run on all three branches with one real form each. Compare review reports vs. today's LLM-filled output.

Step 2 is the load-bearing one — if the deterministic filler works on a real form with one mapping, every later step is a layer on top.

---

## 9. Non-goals

- No change to extractors (Azure DI, AcroForm widget enumeration, mapped widget→label binding).
- No change to writers or vision passes.
- No new field types in `FieldType` Literal.
- No domain-specific keys or labels in any code or prompt — same constraint as today.
- The LLM filler stays in the codebase as a fallback / dev tool.
