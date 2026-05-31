# Pipeline Workflows

Three independent workflows process healthcare claims appeal forms. The right one is chosen by the input PDF:

| Workflow | When to use | Tooling |
|----------|------------|---------|
| **1. Non-editable (scanned)** | Printed/scanned PDF, **no AcroForm widgets** | Azure DI + 3 LLM agents (schema, fill, vision-corrected stamping) |
| **2. AcroForm (direct)** | PDF carries AcroForm widgets **and** widget labels (`/TU` tooltips or text-layer proximity) are reliable | pymupdf widget metadata + LLM fill |
| **3. Mapped AcroForm** | PDF carries AcroForm widgets but labels are noisy/missing, so widget→label binding needs vision | pymupdf widgets + Azure DI + vision LLM mapper + LLM fill |

All three converge on the same `FilledFormSchema` shape and produce a filled PDF plus a `review.json` flagging anything the writer couldn't confidently place.

Pick the branch programmatically with:

```bash
.venv-1/bin/python -c "from claims_parser import pick_branch; print(pick_branch('input/X.pdf'))"
```

---

## Workflow 1 — Non-editable (scanned) branch

Source: printed forms with no fillable widgets. The pipeline must infer fields from OCR alone and stamp values back onto the page at the right coordinates.

### Flow

```
input/<stem>.pdf
        │
        ▼
┌───────────────────────┐
│ extract.py            │  Azure Document Intelligence (prebuilt-layout + KVP)
│ claims_parser/        │  Chunks PDF into ≤2-page segments (F0 cap),
│   extractor.py        │  merges with page offsets
└──────────┬────────────┘
           ▼
 intermediate/<stem>.context.json   (pages, lines, tables, KVPs, selection_marks)
           │
           ▼
┌───────────────────────┐
│ build_schema.py       │  Agent 1 — gpt-5-mini reads OCR text,
│ claims_parser/        │  identifies every fillable input,
│   schema_builder.py   │  produces generic FormSchema
└──────────┬────────────┘
           ▼
 intermediate/<stem>.schema.json    (FormField[]: field_id, label, section,
                                     field_type, options, source_text, …)
           │
           ▼
┌───────────────────────┐
│ fill_schema.py        │  Agent 2 — gpt-5-mini fills every field
│ claims_parser/        │  with plausible fictional values,
│   form_filler.py      │  respects field_type / options / format_hint
└──────────┬────────────┘
           ▼
 intermediate/<stem>.filled.json    (FilledFormField[] with .value populated)
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ write_pdf.py    Agent 3 — three sub-stages                       │
│                                                                  │
│   1. anchor_placer.build_initial_plan                            │
│        match field.source_text → KVP / TextLine polygon          │
│        emit WriteOp(kind=text|tick, page, x, y) in pixels        │
│                                                                  │
│   2. pdf_writer.correct_plan  (N iterations, default 2)          │
│        render page → numbered red circles at each (x,y)          │
│        ask gpt-5-mini: return (dx, dy) per marker                │
│        apply correction                                          │
│                                                                  │
│   3. pdf_writer.apply_plan                                       │
│        page.insert_text   for text fields                        │
│        page.draw_line ×2  for ticks (✓; no font dependency)      │
└──────────┬───────────────────────────────────────────────────────┘
           ▼
 final/<stem>.filled.pdf
 intermediate/<stem>.writeplan.json    (final coords)
 intermediate/<stem>.review.json       (unanchored, uncertain_after_vision, …)
```

### Code walkthrough

| File | Role | Key entry points |
|------|------|------------------|
| [claims_parser/extractor.py](../claims_parser/extractor.py) | Azure DI client + chunking | `extract_document_context(pdf)`, `_chunk_pdf_bytes`, `_ingest_result` |
| [claims_parser/models.py](../claims_parser/models.py) | `DocumentContext`, `PageMetadata`, `TextLine`, `Table`, `KVP`, `SelectionMark` | — |
| [claims_parser/schema_builder.py](../claims_parser/schema_builder.py) | Agent 1: OCR text → `FormSchema` | `build_schema(ctx)` (`client.chat.completions.parse(response_format=FormSchema)`) |
| [claims_parser/schema_models.py](../claims_parser/schema_models.py) | `FormField`, `FormSchema`, `FieldType` literal | `FormField.source_text` is the link to Azure polygons |
| [claims_parser/form_filler.py](../claims_parser/form_filler.py) | Agent 2: fills every field | `fill_form_schema(schema)` — prompt forbids empty values |
| [claims_parser/anchor_placer.py](../claims_parser/anchor_placer.py) | Deterministic initial placement | `build_initial_plan(filled, ctx, dpi)` matches `source_text` to KVP keys / lines |
| [claims_parser/pdf_writer.py](../claims_parser/pdf_writer.py) | Vision correction + stamping | `correct_plan(plan, pdf, n_iterations)`, `apply_plan(pdf, plan, out)` |
| [claims_parser/writer_models.py](../claims_parser/writer_models.py) | `WriteOp(kind: "text"\|"tick", page, x, y, …)`, `WritePlan` | — |
| [claims_parser/review_models.py](../claims_parser/review_models.py) | `ReviewReport`, `ReviewItem` | Reasons: `missing_value`, `unanchored`, `uncertain_after_vision`, `large_residual_correction` |

**The load-bearing trick**: `FormField.source_text` is a verbatim quote from the OCR. Agent 3's anchor placer string-matches it against `KVP.key_text` and `TextLine.text` to recover a polygon. Fields whose `source_text` doesn't match anything are flagged `unanchored` and skipped — they cannot be rescued by vision passes because there's no marker on the page to correct.

### Execute it

```bash
PDF=input/AETNA_Form1.pdf

.venv-1/bin/python extract.py      "$PDF"
.venv-1/bin/python build_schema.py output/intermediate/AETNA_Form1.context.json
.venv-1/bin/python fill_schema.py  output/intermediate/AETNA_Form1.schema.json
.venv-1/bin/python write_pdf.py    "$PDF" \
                                   output/intermediate/AETNA_Form1.filled.json \
                                   --iterations 2
```

Useful flags:
- `write_pdf.py --iterations 0` — skip vision passes, anchor placement only (fast sanity check)
- All CLIs accept `-o <path>` to override the default output

---

## Workflow 2 — AcroForm (direct) branch

Source: PDFs that already carry AcroForm widgets with usable labels (`widget.field_label` populated, or labels recoverable from text-layer proximity). Skips Azure entirely; never runs vision passes for placement.

### Flow

```
input/<stem>.pdf
        │
        ▼
┌─────────────────────────────────┐
│ acroform_extract.py              │  pymupdf widget enumeration
│ claims_parser/                   │  + label inference cascade:
│   acroform_extractor.py          │    Tier 1: widget.field_label (/TU tooltip)
│   acroform_label_inference.py    │    Tier 2: text-layer proximity (no OCR)
│                                  │    Tier 3 (optional): single vision call
│                                  │      per page for residuals
│                                  │  + optional LLM type-refinement pass
└──────────┬───────────────────────┘
           ▼
 intermediate/<stem>.schema.json     (FormSchema)
 intermediate/<stem>.binding.json    (AcroFormBinding: field_id → widget xref(s))
           │
           ▼
┌───────────────────────┐
│ fill_schema.py        │  Agent 2 — SAME module as workflow 1
│ claims_parser/        │
│   form_filler.py      │
└──────────┬────────────┘
           ▼
 intermediate/<stem>.filled.json
           │
           ▼
┌───────────────────────┐
│ acroform_write.py     │  For each field: look up widget by xref,
│ claims_parser/        │  set widget.field_value = …, widget.update()
│   acroform_writer.py  │  Radios: write all siblings "Off", chosen one last
│                       │  --flatten → doc.bake() to make non-editable
└──────────┬────────────┘
           ▼
 final/<stem>.filled.pdf          (editable by default)
 intermediate/<stem>.review.json  (label_inference_failed, missing_binding,
                                   unsettable_widget, …)
```

### Code walkthrough

| File | Role | Key entry points |
|------|------|------------------|
| [claims_parser/acroform_detect.py](../claims_parser/acroform_detect.py) | Branch picker | `is_acroform_pdf(pdf)`, `pick_branch(pdf)` |
| [claims_parser/acroform_extractor.py](../claims_parser/acroform_extractor.py) | Widgets → `(FormSchema, AcroFormBinding)` | `extract_acroform(pdf, refine=True, vision=True)` |
| [claims_parser/acroform_label_inference.py](../claims_parser/acroform_label_inference.py) | 3-tier label cascade | `infer_labels(widgets, page_text, vision_client=None)` |
| [claims_parser/acroform_models.py](../claims_parser/acroform_models.py) | `AcroFormBinding`, `AcroFormFieldBinding`, `OptionBinding` | Ties `FormField.field_id` to widget xref(s); radio options carry `on_value` |
| [claims_parser/form_filler.py](../claims_parser/form_filler.py) | Agent 2 (shared) | `fill_form_schema(schema)` |
| [claims_parser/acroform_writer.py](../claims_parser/acroform_writer.py) | Widget-value writer | `write_acroform(pdf, filled, binding, out, flatten=False)` |

**Why no vision placement**: each widget already has a rect; the writer just calls `widget.field_value = ...` and `widget.update()`. The only LLM in this branch is Agent 2 (fill).

**Radio groups**: a single radio child has its own `on_value` (e.g. `"Yes"`). pymupdf will set the parent's `/V` correctly, but stale `/AS` on the siblings leaves viewers drawing no dot. The writer explicitly forces every sibling to `Off`, then sets the chosen child last so `/V` lands on its on-value.

### Execute it

```bash
PDF=input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf
STEM=ANTHEM_NV_CAID_ClaimsAppealsForm

.venv-1/bin/python acroform_extract.py "$PDF"
.venv-1/bin/python fill_schema.py      "output/intermediate/${STEM}.schema.json"
.venv-1/bin/python acroform_write.py   "$PDF" \
                                       "output/intermediate/${STEM}.filled.json"
```

Useful flags:
- `acroform_extract.py --no-refine` — skip LLM type-refinement (faster, coarser types)
- `acroform_extract.py --no-vision` — skip Tier-3 vision label residuals
- `acroform_write.py --flatten` — bake widgets into static content (PDF becomes non-editable)

---

## Workflow 3 — Mapped AcroForm branch

Source: PDFs with AcroForm widgets where labels are unreliable (anonymous `/T` names like `Text1`, no `/TU`, layouts that defeat proximity heuristics). A vision LLM does the widget→label binding by looking at the rendered page with widget rectangles overlaid.

### Flow

```
input/<stem>.pdf
        │
        ├──────────────────────────┐
        ▼                          ▼
┌───────────────────────┐    ┌──────────────────────────┐
│ extract.py            │    │ widget_extract.py        │
│ claims_parser/        │    │ claims_parser/           │
│   extractor.py        │    │   widget_extractor.py    │
│ Azure DI (chunked)    │    │ pymupdf widget catalog   │
└──────────┬────────────┘    └──────────┬───────────────┘
           ▼                            ▼
 <stem>.context.json          intermediate/mapped/<stem>.widgets.json
           │                            │
           └────────────┬───────────────┘
                        ▼
┌──────────────────────────────────────────────────────────┐
│ map_widgets.py                                           │
│ claims_parser/widget_mapper.py                           │
│                                                          │
│ Per page-chunk (default 4 pages):                        │
│   1. Render page → PNG at 144 dpi, base64 image_url      │
│   2. Build prompt: widgets in this chunk + KVPs/lines    │
│      from Azure context for the same pages               │
│   3. gpt-5-mini structured output:                       │
│      WidgetBinding(field_id, label, on_value,            │
│                    confidence, label_page, rect)         │
│   4. _validate_chunk:                                    │
│        spatial sanity (≤100pt from claimed label rect),  │
│        option-label uniqueness within a radio group      │
│                                                          │
│ build_schema_from_mapping:                                │
│   collapse radio siblings into one radio_group field,    │
│   classify text vs checkbox vs long_text by widget type  │
│   + rect height, snake_case + uniqify field_ids          │
└──────────┬───────────────────────────────────────────────┘
           ▼
 intermediate/mapped/<stem>.mapping.json  (WidgetMapping)
 intermediate/mapped/<stem>.schema.json   (FormSchema with bindings inline
                                            on each FormField.widget_bindings)
           │
           ▼
┌───────────────────────┐
│ fill_schema.py        │  Agent 2 — SAME module
│ claims_parser/        │  Strips widget_bindings before sending to LLM,
│   form_filler.py      │  re-attaches them on the returned FilledFormSchema
└──────────┬────────────┘
           ▼
 intermediate/mapped/<stem>.filled.json
           │
           ▼
┌───────────────────────┐
│ mapped_write.py       │  Reads field.widget_bindings directly
│ claims_parser/        │  (no separate binding file).
│   mapped_writer.py    │  Same xref-lookup + radio /AS handling as workflow 2.
└──────────┬────────────┘
           ▼
 final/mapped/<stem>.filled.pdf
 intermediate/mapped/<stem>.review.json   (widget_low_confidence,
                                            widget_unmapped, unsettable_widget)
```

### Code walkthrough

| File | Role | Key entry points |
|------|------|------------------|
| [claims_parser/widget_extractor.py](../claims_parser/widget_extractor.py) | pymupdf widget enumeration → `WidgetCatalog` | `extract_widget_catalog(pdf)`; `_TYPE_MAP` collapses pymupdf widget kinds into 6 categories |
| [claims_parser/widget_models.py](../claims_parser/widget_models.py) | `WidgetCatalog`, `WidgetEntry` | `WidgetEntry.on_value` carries radio `/AS` |
| [claims_parser/extractor.py](../claims_parser/extractor.py) | Azure context (reused from workflow 1) | `extract_document_context(pdf)` |
| [claims_parser/widget_mapper.py](../claims_parser/widget_mapper.py) | Vision mapper + schema build | `map_widgets(pdf, ctx, catalog, model)`, `build_schema_from_mapping(catalog, mapping)`, `_validate_chunk` |
| [claims_parser/mapping_models.py](../claims_parser/mapping_models.py) | `WidgetMapping`, `WidgetBinding` | `confidence`, `label_page`, `on_value` per binding |
| [claims_parser/schema_models.py](../claims_parser/schema_models.py) | `FormField.widget_bindings: list[WidgetBinding]` | Bindings travel inline on the schema in this branch |
| [claims_parser/form_filler.py](../claims_parser/form_filler.py) | Agent 2 (shared) | Pops `widget_bindings` before the LLM call, re-attaches after parsing |
| [claims_parser/mapped_writer.py](../claims_parser/mapped_writer.py) | Widget-value writer | `write_mapped(pdf, filled, out, flatten=False)`; reads bindings from each `FilledFormField.widget_bindings` |

**Key differences from workflow 2**:
- Bindings live **on the schema field** (`FormField.widget_bindings`), not in a sibling `binding.json`.
- Confidence is per-binding. Anything below `LOW_CONFIDENCE_THRESHOLD = 0.5` in `mapped_writer.py` lands in the review report (still gets written).
- Page chunking for the mapper (`MAPPER_CHUNK_PAGES = 4`) is independent of Azure's 2-page chunking; the mapper just needs the rendered page bytes and the per-page KVP/line text.

### Execute it

```bash
PDF=input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf
STEM=ANTHEM_NV_CAID_ClaimsAppealsForm

# Azure context (shared with workflow 1)
.venv-1/bin/python extract.py        "$PDF"

# pymupdf widget catalog
.venv-1/bin/python widget_extract.py "$PDF"

# Vision LLM widget → label binding + schema build
.venv-1/bin/python map_widgets.py    "$PDF" \
                                     "output/intermediate/${STEM}.context.json" \
                                     "output/intermediate/mapped/${STEM}.widgets.json"

# Agent 2 (shared)
.venv-1/bin/python fill_schema.py    "output/intermediate/mapped/${STEM}.schema.json" \
                                     -o "output/intermediate/mapped/${STEM}.filled.json"

# Write into AcroForm widgets
.venv-1/bin/python mapped_write.py   "$PDF" \
                                     "output/intermediate/mapped/${STEM}.filled.json"
```

Useful flags:
- `map_widgets.py -m <model>` — override mapper LLM (default `gpt-5-mini`)
- `mapped_write.py --flatten` — bake widgets into static content

---

## Shared concerns

- **Secrets** live in `parser.env` only (`AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`, `AZURE_DOCUMENT_INTELLIGENCE_KEY`, `OPENAI_API_KEY`). Loaded by [claims_parser/config.py](../claims_parser/config.py).
- **Page-count agnostic**: the Azure extractor chunks the PDF into ≤2-page segments to stay under the F0 per-document cap. The mapper chunks at 4 pages. Both merge with page offsets — no module assumes a fixed page count.
- **Every field is filled unconditionally** in workflow 1/2/3 — Agent 2's prompt forbids empty values so the writer can be validated end-to-end. Conditional sub-questions ("If yes, date:") are treated as if the condition applied.
- **Tick marks (✓), not X**, by default. Switch to "X" only when the form text explicitly demands it.
- **Review reports** are emitted by every writer. Triage by `reason`: any item there is something a human should sanity-check before sending the filled PDF anywhere.
