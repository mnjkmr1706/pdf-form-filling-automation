# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A 3-agent LLM pipeline that processes **non-editable healthcare insurance claims appeal forms** (Aetna, UHC, Anthem, BCBS, Cigna, etc. — no AcroForm fields). Pipeline stages:

1. **Agent 1 — Schema Builder**: OCR text → generic JSON `FormSchema` describing every fillable input.
2. **Agent 2 — Form Filler**: `FormSchema` → populate with dummy-but-relevant values respecting `field_type` / `format_hint` / `options`. **Every field is filled unconditionally** (no skipping of fields that appear conditional on another field) so the writer can be validated end-to-end.
3. **Agent 3 — PDF Writer**: write values back into the PDF without disturbing layout/labels. Two stages:
   - `anchor_placer.build_initial_plan` — deterministic placement from Azure polygons (KVP key/value polygons for text fields, selection-mark polygons for checkbox/radio options).
   - `pdf_writer.correct_plan` — N labeled-marker vision passes: render each page with numbered red circles at predicted (x, y), ask `gpt-5-mini` to return (dx, dy) per marker, apply. Default 2 iterations.
   - `pdf_writer.apply_plan` — stamps text via `page.insert_text` and ticks via `page.draw_line` (two line segments forming ✓; no font dependency). Default tick — only switch to "X" if the form text explicitly requires it.

## Non-Negotiable Constraints

These come directly from the user and override any default instinct toward convenience:

- **No hardcoding of field names, labels, sections, issuers, or option values anywhere in code or prompts.** Every form is different. A concept that's a textbox on one form may be a checkbox or radio on another — do not encode form-specific assumptions.
- **Code and prompts must stay domain-generic.** Pydantic field descriptions and LLM system prompts must not contain example labels/sections drawn from any particular form. The `FieldType` Literal in [claims_parser/schema_models.py](claims_parser/schema_models.py) is the only allowed enum of values; everything else is derived from the input.
- **Cache-first workflow.** Azure Document Intelligence runs **once per PDF**; the resulting `DocumentContext` is saved to `<name>.context.json` and all downstream agents `load_context()` from disk. Never re-run Azure to drive a downstream agent.
- **Code must handle forms of any page count.** Fields on the last page must be extracted just like those on the first. The extractor chunks the PDF into ≤`AZURE_CHUNK_PAGES`-page segments to stay under the F0 per-document cap and merges chunk results with page-number offsets. Do not assume 2 pages anywhere.
- **Fill every field.** Agent 2's prompt explicitly forbids leaving any value empty. Treat every conditional sub-question ("If yes, …") as if its condition applied.
- **Tick marks, not X**, for checkbox/radio selections by default. Use "X" only if the form text explicitly says to mark with an X.
- **Secrets in [parser.env](parser.env) only** (`AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`, `AZURE_DOCUMENT_INTELLIGENCE_KEY`, `OPENAI_API_KEY`). Never log or commit.

## Two branches

The pipeline branches at ingestion based on whether the input PDF carries AcroForm widgets:

- **AcroForm branch** (`is_acroform_pdf == True`): widgets carry their own type, rect, and (for choice fields) options. Skip Azure, skip Agent 1 OCR-based schema inference, skip Agent 3 anchor + vision passes. Use widget metadata directly. See `acroform_extract.py` → `fill_schema.py` → `acroform_write.py`.
- **Non-editable (scanned/printed) branch**: original pipeline. `extract.py` → `build_schema.py` → `fill_schema.py` → `write_pdf.py`. Untouched.

`fill_schema.py` (Agent 2) is shared — both branches converge on the same `FormSchema` shape.

Use `claims_parser.is_acroform_pdf(path)` or `pick_branch(path)` to decide which CLIs to run.

## Folder layout

```
input/                          source PDFs (one per form)
output/
  intermediate/                 every JSON artifact produced by any agent
    <stem>.context.json         (non-editable branch: Azure)
    <stem>.schema.json          (Agent 1 OR AcroForm extractor)
    <stem>.binding.json         (AcroForm branch only: widget map)
    <stem>.filled.json          (Agent 2, shared)
    <stem>.writeplan.json       (non-editable branch: Agent 3 final coords)
    <stem>.review.json          (Agent 3 / AcroForm writer: human-review flags)
  final/
    <stem>.filled.pdf           the deliverable
```

All four CLIs default their outputs to these paths. Explicit `-o` / `--plan-out` / `--review-out` flags still override. Place new input PDFs in `input/`.

## Architecture — non-editable branch

```
input/<name>.pdf ─[extract.py]──▶ intermediate/<name>.context.json
                                   DocumentContext
                                   (Azure layout + KVPs + selection_marks, chunked)

intermediate/<name>.context.json ─[build_schema.py]──▶ intermediate/<name>.schema.json
                                   Agent 1 / gpt-5-mini

intermediate/<name>.schema.json ──[fill_schema.py]───▶ intermediate/<name>.filled.json
                                   Agent 2 / gpt-5-mini

input/<name>.pdf + filled.json + context.json ─[write_pdf.py]─▶ final/<name>.filled.pdf
                                                                 + intermediate/<name>.writeplan.json
                                                                 + intermediate/<name>.review.json
                                                Agent 3: anchor → vision×N → stamp
```

## Architecture — AcroForm branch

```
input/<name>.pdf ─[acroform_extract.py]──▶ intermediate/<name>.schema.json
                                          + intermediate/<name>.binding.json
                  widgets via pymupdf → label inference cascade
                  (TU tooltip → text-layer proximity → optional vision residuals)
                  + optional LLM type refinement

intermediate/<name>.schema.json ──[fill_schema.py]───▶ intermediate/<name>.filled.json
                                   Agent 2 / gpt-5-mini   (SAME module as non-editable)

input/<name>.pdf + filled.json + binding.json ─[acroform_write.py]─▶ final/<name>.filled.pdf
                                                                      + intermediate/<name>.review.json
                                                widget.field_value = ... ; widget.update()
                                                (no anchor math, no vision passes)
```

The AcroForm branch never calls Azure and never runs vision passes for placement. Output is editable by default; pass `--flatten` to bake widgets into static content.

Package layout under [claims_parser/](claims_parser/):

Shared:
- [config.py](claims_parser/config.py) — loads `parser.env` via `python-dotenv`.
- [schema_models.py](claims_parser/schema_models.py) — generic `FormField` / `FormSchema`. `FieldType` is a closed Literal of HTML-input-style categories. `FormField.source_text` is a verbatim quote that lets later stages map a field back to its source.
- [filler_models.py](claims_parser/filler_models.py) / [form_filler.py](claims_parser/form_filler.py) — Agent 2. Same module for both branches.
- [review_models.py](claims_parser/review_models.py) — `ReviewReport` / `ReviewItem`. Reasons: `missing_value`, `unanchored`, `uncertain_after_vision`, `large_residual_correction`, `label_inference_failed`, `missing_binding`, `unsettable_widget`.

Non-editable branch:
- [models.py](claims_parser/models.py) — `DocumentContext` and sub-models (`PageMetadata`, `TextLine`, `Table`, `TableCell`, `KVP`, `SelectionMark`).
- [extractor.py](claims_parser/extractor.py) — Azure DI wrapper. Chunks the PDF into `AZURE_CHUNK_PAGES`-page segments via pymupdf and merges results with page offsets.
- [schema_builder.py](claims_parser/schema_builder.py) — Agent 1 (OCR-driven).
- [anchor_placer.py](claims_parser/anchor_placer.py) — deterministic placement using KVP/selection-mark polygons.
- [writer_models.py](claims_parser/writer_models.py) — `WriteOp` with `kind: Literal["text", "tick"]`.
- [pdf_writer.py](claims_parser/pdf_writer.py) — vision correction + final stamping.

AcroForm branch:
- [acroform_detect.py](claims_parser/acroform_detect.py) — `is_acroform_pdf` / `pick_branch`.
- [acroform_models.py](claims_parser/acroform_models.py) — `AcroFormBinding` / `AcroFormFieldBinding` / `OptionBinding`. The binding ties `FormField.field_id` back to the widget xref(s) so the writer can update the right widget.
- [acroform_label_inference.py](claims_parser/acroform_label_inference.py) — Tier-1 (`widget.field_label`) + Tier-2 (text-layer proximity, no OCR) + Tier-3 (single vision call per page with unresolved widgets).
- [acroform_extractor.py](claims_parser/acroform_extractor.py) — widgets → `(FormSchema, AcroFormBinding)`. Optionally one LLM call to refine coarse `text` types into specific `FieldType` values (date / phone / identifier / etc.). Radio buttons are grouped by parent `field_name`; each `OptionBinding` records the per-radio `on_value`.
- [acroform_writer.py](claims_parser/acroform_writer.py) — `write_acroform(pdf, filled, binding, output, flatten=False)`. Sets `widget.field_value` and calls `widget.update()`. Optional `doc.bake()` flatten.

CLIs at the repo root:

Non-editable:
- [extract.py](extract.py), [build_schema.py](build_schema.py), [fill_schema.py](fill_schema.py), [write_pdf.py](write_pdf.py)

AcroForm:
- [acroform_extract.py](acroform_extract.py) — PDF → `<stem>.schema.json` + `<stem>.binding.json`. Flags: `--no-refine`, `--no-vision`.
- [fill_schema.py](fill_schema.py) — reused unchanged.
- [acroform_write.py](acroform_write.py) — PDF + filled + binding → editable filled PDF + review. Flag: `--flatten` to bake widgets.

Anything new should follow the same shape: a module under `claims_parser/` exporting through [`__init__.py`](claims_parser/__init__.py), plus a thin CLI at the root.

## Bridge between agents: `source_text`

`FormField.source_text` is the load-bearing link between semantic (LLM-produced) and spatial (Azure-produced) views of the form. Agent 3 string-matches it against `KVP.key_text` and `TextLine.text` from the cached `DocumentContext` to recover polygons. Keep this property intact — don't paraphrase or transform `source_text`. Fields whose `source_text` doesn't match anything are reported as `unanchored` in the review and skipped from stamping.

## Human-review report

`write_pdf.py` always emits `<stem>.review.json` alongside the filled PDF. A field appears in the report when:

- **`missing_value`** — Agent 2 returned no value (should be rare now that the prompt forbids empties).
- **`unanchored`** — Agent 3's matcher couldn't locate a polygon for the field. Needs matcher improvement (fuzzy/positional/embedding) or a section-scoped fallback; no number of vision passes can rescue these because there's no marker on the PDF.
- **`uncertain_after_vision`** — after the final vision pass, the model still reports `ok=false` for the marker.
- **`large_residual_correction`** — cumulative |dx|+|dy| over all iterations exceeded the threshold (`LARGE_RESIDUAL_PX` in `pdf_writer.py`). Often indicates an initially wrong anchor or a dense nested-checkbox region where marker disambiguation is hard.

## Commands

The active virtualenv is `.venv-1`. Use `uv` for dependency management.

```bash
# Detect which branch to use
.venv-1/bin/python -c "from claims_parser import pick_branch; print(pick_branch('input/X.pdf'))"

# Non-editable branch (scanned / printed forms)
.venv-1/bin/python extract.py      input/AETNA_Form1.pdf
.venv-1/bin/python build_schema.py output/intermediate/AETNA_Form1.context.json
.venv-1/bin/python fill_schema.py  output/intermediate/AETNA_Form1.schema.json
.venv-1/bin/python write_pdf.py    input/AETNA_Form1.pdf \
                                   output/intermediate/AETNA_Form1.filled.json \
                                   --iterations 2

# AcroForm branch (editable PDFs)
.venv-1/bin/python acroform_extract.py input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf
.venv-1/bin/python fill_schema.py      output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.schema.json
.venv-1/bin/python acroform_write.py   input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf \
                                       output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.filled.json

# Mapped AcroForm branch with fingerprint cache (skip the vision LLM on repeat forms)
.venv-1/bin/python map_widgets_cached.py input/<name>.pdf \
                                         output/intermediate/<name>.context.json \
                                         output/intermediate/mapped/<name>.widgets.json

# Useful flags
#   write_pdf.py             --iterations 0   skip vision pass (anchor only)
#   acroform_extract.py      --no-refine      skip LLM type-refinement
#   acroform_extract.py      --no-vision      skip Tier-3 vision label residuals
#   acroform_write.py        --flatten        bake widgets into static content
#   map_widgets_cached.py    --no-cache       bypass cache (skip read AND write)
#   map_widgets_cached.py    --rebuild        force re-derive and overwrite cache entry
#   map_widgets_cached.py    --strict-cache   exit non-zero on cache miss

# Dependencies
uv add <package>
uv sync
```

## External service notes

- **Azure F0 tier** caps each document at 2 pages. The extractor handles arbitrary page counts by chunking and merging — this is the canonical approach in this codebase; do not undo it. Each chunk counts as one document toward the F0 daily quota (500 pages/day).
- **OpenAI**: default model is `gpt-5-mini` (Tier 1: 60K TPM / 10 RPM). Structured outputs go through `client.chat.completions.parse(response_format=<PydanticModel>)`. Vision passes send PNGs as base64 data URLs in `image_url` content parts. Always check `response.choices[0].message.parsed` is not None before using it.

## Future scope (not yet built)

- **Better `source_text` matcher** — fuzzy/embedding match for fields currently flagged `unanchored`. Section-scoped fallback for `long_text` fields (place inside the largest empty rectangle within the section's polygon).
- **Per-field crop-refine pass** — for fields still flagged after the page-level vision passes, crop a small region around the current anchor and ask the model for a refined point in crop-local coordinates. Reserved for residuals only; not a blanket strategy. See conversation history for rationale.
- **"Mark with X" heuristic** — detect form-text instructions like "mark with X" / "check with ✓" and switch the tick style accordingly. Currently always uses ✓.
- **Long-text wrapping** — single-line `insert_text` will overflow narrow boxes for `long_text` fields. Need a wrapping writer (`page.insert_textbox`) when the field has a known value-polygon.
- **Multi-form batch CLI** — iterate over a folder of PDFs, run the full pipeline per file, aggregate review reports.
