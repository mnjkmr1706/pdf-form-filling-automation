# Mapped AcroForm Branch — Spec

A vision-LLM workflow that fills any AcroForm (editable) healthcare claims
appeal PDF by binding every widget to its printed label, then writing values
via PyMuPDF. Output stays editable. No form-specific logic.

## Pipeline

```
input/<name>.pdf
   ├─ extract.py        → output/intermediate/<name>.context.json     (Azure DI)
   └─ widget_extract.py → output/intermediate/mapped/<name>.widgets.json (pymupdf)

   <pdf> + <name>.context.json + <name>.widgets.json
                ↓
        map_widgets.py  → output/intermediate/mapped/<name>.mapping.json
                          output/intermediate/mapped/<name>.schema.json

        fill_schema.py  → output/intermediate/mapped/<name>.filled.json

        mapped_write.py → output/final/mapped/<name>.filled.pdf
                          output/intermediate/mapped/<name>.review.json
```

## Repo layout (this branch only)

```
pdf_parser/
├── extract.py                          # CLI, runs Azure DI (reused)
├── widget_extract.py                   # CLI, runs pymupdf widget catalog
├── map_widgets.py                      # CLI, runs vision LLM mapper
├── fill_schema.py                      # CLI, runs Agent 2 filler (reused)
├── mapped_write.py                     # CLI, writes filled PDF
├── parser.env                          # secrets, gitignored
└── claims_parser/
    ├── config.py                       # loads AZURE_* + OPENAI_API_KEY
    ├── models.py                       # DocumentContext, KVP, TextLine, Table, SelectionMark, Polygon
    ├── extractor.py                    # Azure DI prebuilt-layout + KEY_VALUE_PAIRS, chunked 2-pages
    ├── schema_models.py                # FormField, FormSchema, FieldType  (carries widget_bindings)
    ├── filler_models.py                # FilledFormField, FilledFormSchema (extends FormField)
    ├── form_filler.py                  # Agent 2; strips widget_bindings before LLM, re-attaches after
    ├── review_models.py                # ReviewItem, ReviewReport, ReviewReason
    ├── widget_models.py                # Widget, WidgetCatalog, WidgetRect, WidgetType
    ├── widget_extractor.py             # pymupdf widget enumeration
    ├── mapping_models.py               # WidgetBinding, WidgetMapping, LabelSource
    ├── widget_mapper.py                # vision LLM mapper + chunk validator + build_schema_from_mapping
    └── mapped_writer.py                # AcroForm writer with radio /AS fix
```

## End-to-end commands

```bash
.venv-1/bin/python extract.py        input/<name>.pdf
.venv-1/bin/python widget_extract.py input/<name>.pdf
.venv-1/bin/python map_widgets.py    input/<name>.pdf \
    output/intermediate/<name>.context.json \
    output/intermediate/mapped/<name>.widgets.json
.venv-1/bin/python fill_schema.py    output/intermediate/mapped/<name>.schema.json \
    -o output/intermediate/mapped/<name>.filled.json
.venv-1/bin/python mapped_write.py   input/<name>.pdf \
    output/intermediate/mapped/<name>.filled.json
```

## Dependencies

- Python ≥ 3.10
- `pymupdf`, `openai>=1.50`, `pydantic>=2`, `python-dotenv`, `azure-ai-documentintelligence>=1.0`
- `parser.env` at repo root:
  ```
  AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT="https://<resource>.cognitiveservices.azure.com/"
  AZURE_DOCUMENT_INTELLIGENCE_KEY="..."
  OPENAI_API_KEY="..."
  ```

## Constants (in `widget_mapper.py` and `mapped_writer.py`)

| Name | Value | Where | Meaning |
|------|-------|-------|---------|
| `DEFAULT_MODEL` | `"gpt-5-mini"` | mapper | vision model |
| `CHUNK_PAGES` | `4` | mapper | pages per LLM call |
| `RENDER_DPI` | `144` | mapper | page render DPI for vision |
| `SPATIAL_TOLERANCE_PT` | `100.0` | mapper | label↔widget distance gate (PDF pts); over → confidence −0.2 |
| `LONG_TEXT_MIN_HEIGHT_PT` | `30.0` | mapper | text widget height threshold → `long_text` vs `text` |
| `LOW_CONFIDENCE_THRESHOLD` | `0.5` | writer | bindings under this go to review |
| `AZURE_CHUNK_PAGES` | `2` | extractor | F0 tier per-document page cap |

## Module specs

### `claims_parser/models.py` (existing — referenced by this branch)

```python
Polygon = list[float]                       # flat [x1,y1,x2,y2,x3,y3,x4,y4] in PDF points

class TextLine:        text, page, polygon
class KVP:             key_text, key_page, key_polygon, value_text, value_page, value_polygon, confidence
class TableCell:       row, column, text, page, polygon, row_span, column_span, kind
class Table:           page, row_count, column_count, cells
class SelectionMark:   page, state ("selected"|"unselected"), polygon, confidence
class PageMetadata:    page_number, width, height, unit, line_count
class DocumentContext: file_name, file_path, model_id, full_text, pages, lines, tables,
                       key_value_pairs, selection_marks
```

### `claims_parser/extractor.py` (existing — used here)

Runs Azure `prebuilt-layout` with `DocumentAnalysisFeature.KEY_VALUE_PAIRS`. Chunks
source PDF into `AZURE_CHUNK_PAGES=2` page segments via pymupdf, merges results with
page-number offset. Public API:

```python
extract_document_context(pdf_path) -> DocumentContext
save_context(ctx, path) -> Path
load_context(path) -> DocumentContext
```

### `claims_parser/schema_models.py`

```python
FieldType = Literal[
  "text","long_text","date","time","phone","email","address","number","currency",
  "identifier","signature","checkbox","radio_group"
]

class FormField:
    field_id: str                         # snake_case, unique within form
    label: str                            # verbatim printed label, trailing ':' stripped
    section: Optional[str] = None
    field_type: FieldType
    options: Optional[list[str]] = None   # required for checkbox/radio_group, null otherwise
    format_hint: Optional[str] = None
    required: bool
    source_text: str                      # verbatim quote from OCR
    widget_bindings: Optional[list[WidgetBinding]] = None   # populated only by this branch

class FormSchema:
    form_title: Optional[str]
    issuer: Optional[str]
    sections: list[str]
    fields: list[FormField]
```

### `claims_parser/filler_models.py` (existing)

```python
class FilledFormField(FormField):
    value: Union[str, list[str], None]

class FilledFormSchema:
    form_title, issuer, sections, fields: list[FilledFormField]
```

Because `FilledFormField` extends `FormField`, it carries `widget_bindings` through unchanged.

### `claims_parser/form_filler.py` (modified for this branch)

CLI/public API unchanged. Internal change inside `fill_form_schema()`:

```python
schema_dict = schema.model_dump()
for f in schema_dict.get("fields", []):
    f.pop("widget_bindings", None)            # never sent to LLM
user_msg = json.dumps(schema_dict, indent=2)
# ... LLM call returns FilledFormSchema (widget_bindings = None in response) ...
bindings_by_id = {f.field_id: f.widget_bindings for f in schema.fields}
for ff in filled.fields:
    if bindings_by_id.get(ff.field_id) is not None:
        ff.widget_bindings = bindings_by_id[ff.field_id]
```

System prompt for Agent 2 is unchanged from the existing project (fills every field
with internally-consistent fictional values respecting `field_type` / `format_hint` /
`options`).

### `claims_parser/widget_models.py`

```python
WidgetType = Literal["text","checkbox","radio","choice","signature","button"]

class WidgetRect:    page: int; x0,y0,x1,y1: float
class Widget:
    field_name: str
    widget_type: WidgetType
    rect: WidgetRect
    xref: int
    tu_label: Optional[str]            # author-set tooltip, hint only
    choice_values: Optional[list[str]] # for choice widgets
    on_value: Optional[str]            # widget.on_state(); for checkbox/radio
class WidgetCatalog:
    file_name: str
    page_count: int
    page_sizes_pt: list[tuple[float,float]]
    widgets: list[Widget]
```

### `claims_parser/widget_extractor.py`

Pure pymupdf enumeration:

```python
_TYPE_MAP = {
    "text": "text", "checkbox": "checkbox", "radiobutton": "radio",
    "listbox": "choice", "combobox": "choice",
    "signature": "signature", "pushbutton": "button",
}

def extract_widget_catalog(pdf_path) -> WidgetCatalog:
    open doc with pymupdf
    for page_idx in 1..page_count:
        record (width, height) from page.rect
        for w in page.widgets():
            classify via _TYPE_MAP from w.field_type_string.lower(); default "text"
            on_value = w.on_state() if widget_type in ("checkbox","radio") else None
            choice_values = list(w.choice_values) if present else None
            tu_label = w.field_label or None
            Widget(field_name=w.field_name or f"_unnamed_{w.xref}",
                   widget_type=..., rect=WidgetRect(page=page_idx, ...),
                   xref=w.xref, tu_label=..., choice_values=..., on_value=...)

save_catalog(catalog, path); load_catalog(path)
```

### `claims_parser/mapping_models.py`

```python
LabelSource = Literal["kvp","text_line","table_cell","image_only"]

class WidgetBinding:
    widget_field_name: str              # echoes Widget.field_name
    widget_xref: int                    # echoes Widget.xref (canonical id)
    semantic_field_id: str              # snake_case from label_text
    label_text: str                     # verbatim printed label
    label_source: LabelSource
    label_polygon: Optional[Polygon]    # null iff label_source == "image_only"
    label_page: int                     # == widget rect page
    confidence: float                   # 0..1
    reasoning: str                      # one sentence
    option_label: Optional[str]         # only set for radio option / split-choice checkboxes
    on_value: Optional[str]             # POPULATED BY VALIDATOR (not LLM) from catalog

class WidgetMapping:
    file_name: str
    model: str
    chunk_count: int
    bindings: list[WidgetBinding]
    unmapped_widget_field_names: list[str]

# Internal per-chunk response shape used as response_format:
class _LLMChunkResponse:
    bindings: list[WidgetBinding]
    unmapped_widget_field_names: list[str]
```

### `claims_parser/widget_mapper.py`

#### System prompt (VERBATIM — do not paraphrase)

```
You bind every fillable AcroForm widget on a PDF page to the printed label that
names it on the form.

INPUTS PER CALL
- One or more page images: the authoritative visual rendering of the pages.
- A DI hint set: labels the layout OCR detected on these pages — KVPs,
  text lines, table cells, selection marks. Each carries a polygon in PDF
  points.
- A widget catalog: every input field on these pages with its rect in PDF
  points, widget_type, opaque PDF field_name, optional author-set tu_label,
  on_value, choice_values, and xref.

COORDINATE SYSTEM
- Both page images and PDF polygons use top-left origin with y growing
  downward. All measurements are in PDF points (1 pt = 1/72 inch).
- Page sizes (width, height) in points are provided so you can relate
  image pixels to PDF points.

OUTPUT CONTRACT
- Emit one WidgetBinding per widget OR list the widget's field_name in
  `unmapped_widget_field_names`. Every widget in the input MUST appear in
  exactly one of the two outputs. Never silently drop a widget.
- label_text: the printed label, copied verbatim. Must appear verbatim in
  the DI hint set OR be plainly visible on the page image. Never paraphrase.
- label_source: "kvp", "text_line", or "table_cell" if drawn from the DI
  hint set; "image_only" if the label is visible on the image but missing
  from DI.
- label_polygon: copy verbatim from the DI hint. Null ONLY when
  label_source is "image_only".
- label_page: the page on which the label appears. Must equal the
  widget's page.
- semantic_field_id: lowercase snake_case derived from the label, unique
  within the form. Radio siblings sharing a label share this id.
- confidence: your honest estimate the binding is correct, 0 to 1.
- reasoning: one short sentence naming the evidence (e.g. "label sits
  immediately left of the widget rect and matches DI KVP key").

RADIO GROUPS
- Widgets with widget_type=="radio" sharing the same field_name are option
  siblings of one logical question.
- Emit one binding per sibling widget. Each binding's widget_xref points to
  one specific option widget — that is the distinguishing key.
- All siblings share label_text (the parent question), label_polygon,
  label_page, and semantic_field_id.
- option_label: the printed choice text adjacent to THIS specific option
  widget (e.g. the word next to the "○" — usually "Yes" / "No" / a choice).

CHECKBOXES
- Treat each checkbox widget as its own logical field. Its label_text is
  the printed prompt that names the choice (often the text immediately to
  the right of the box).
- Set option_label only if the checkbox is one of several whose label_text
  is a multi-word prompt and you want to record the short choice text
  separately. Otherwise null.

CHOICE / DROPDOWN / TEXT / SIGNATURE
- Single binding per widget. label_text is the visible label that names
  the input (typically printed left of or above the widget).
- option_label MUST be null.

HARD RULES
1. Every widget in the catalog MUST end in bindings ∪ unmapped_widget_field_names.
2. label_page MUST equal the widget's rect page. Never bind across pages.
3. tu_label is a hint only. The printed label on the page wins.
4. Never paraphrase or normalize label_text. Copy verbatim.
5. Never invent a label that is not present on THIS form's image.
6. Read THIS form only. Do not assume conventions from any other form.
7. If no plausible printed label is visible near a widget, mark it unmapped.
8. Match by spatial adjacency on the image first; confirm against the DI
   hint set.
```

#### Chunking

Process pages in groups of `CHUNK_PAGES=4`. Widgets are page-pinned (each widget
has exactly one page), so chunks partition widgets exactly. Per chunk:

1. Render each page to PNG via `doc[idx].get_pixmap(dpi=144).tobytes("png")` →
   base64 → `data:image/png;base64,...`
2. Build textual payload (sent as one `text` content block, JSON-compact):
   ```
   { "pages": [..page numbers..],
     "page_sizes_pt": { page_num: (w_pt, h_pt) },
     "widgets": [Widget.model_dump() ...],
     "di_hints": {
         "key_value_pairs":  [KVP.model_dump()         ... on these pages],
         "text_lines":       [TextLine.model_dump()    ... on these pages],
         "table_cells":      [TableCell.model_dump()   ... on these pages, with text.strip()],
         "selection_marks":  [SelectionMark.model_dump() ... on these pages]
     } }
   ```
3. OpenAI call:
   ```python
   client.chat.completions.parse(
       model="gpt-5-mini",
       messages=[
           {"role": "system", "content": SYSTEM_PROMPT},
           {"role": "user",   "content": [
               {"type":"text","text": payload_json},
               {"type":"image_url","image_url":{"url": data_url, "detail":"high"}},
               ...
           ]}
       ],
       response_format=_LLMChunkResponse,
   )
   ```
4. Validate the chunk response, append to running totals.

#### Validation (deterministic, after each chunk)

For each binding the LLM returned:

1. Resolve widget by `widget_xref` (preferred) or `widget_field_name`. If
   neither resolves to a widget in this chunk's catalog → drop the binding.
2. If the widget was already bound in this chunk → drop (duplicate).
3. If `b.label_page != widget.rect.page` → move widget to `unmapped`.
4. If `b.label_source != "image_only"` AND `b.label_polygon` is set, compute
   the minimum 2-D distance between widget rect and the polygon's axis-aligned
   bounding box. If > `SPATIAL_TOLERANCE_PT` (100 pt) → `confidence -= 0.2`
   (floor at 0).
5. Force-overwrite `widget_xref` and `widget_field_name` from the catalog
   widget.
6. Populate `on_value`: copy `widget.on_value` from the catalog IFF the
   widget's type is `"checkbox"` or `"radio"`; else `None`. The LLM never
   produces `on_value`.

After processing all bindings: any catalog widget not in `seen_xrefs` and not
already in the LLM-provided unmapped list is appended to `unmapped`.
Deduplicate `unmapped` preserving order.

#### `build_schema_from_mapping(catalog, mapping) -> FormSchema`

Group `mapping.bindings` by `semantic_field_id`. For each group:

- Resolve catalog widgets for each binding (by xref). Drop missing.
- If `widget_types == {"radio"}` and group size > 1 → emit ONE `FormField`:
  - `field_type = "radio_group"`
  - `label = group_bindings[0].label_text`
  - `options = [ b.option_label or b.on_value or "Option" for b in group_bindings ]`
  - `widget_bindings = group_bindings`
- Otherwise, for each binding/widget independently, emit ONE `FormField`:
  - Type+options resolved by widget type:
    - `checkbox`  → `("checkbox", [b.label_text])`
    - `choice`    → `("radio_group", list(w.choice_values or []))`
    - `signature` → `("signature", None)`
    - `text`/everything else → `("long_text", None)` if rect height > 30 pt else `("text", None)`
  - `widget_bindings = [b]`

Field-id uniqueness: snake-case the raw id, then suffix `_2`, `_3`, ... if
needed. Top-level: `form_title=None, issuer=None, sections=[]`.

#### Public API

```python
DEFAULT_MODEL = "gpt-5-mini"

map_widgets(pdf_path, doc_ctx, catalog, model=DEFAULT_MODEL, client=None) -> WidgetMapping
build_schema_from_mapping(catalog, mapping) -> FormSchema
save_mapping(mapping, path) / load_mapping(path)
```

### `claims_parser/mapped_writer.py`

```python
LOW_CONFIDENCE_THRESHOLD = 0.5

def write_mapped(pdf_path, filled: FilledFormSchema, output_path, flatten=False) -> ReviewReport
```

Algorithm per field:

1. `bindings = field.widget_bindings or []`. Empty → review `widget_unmapped`,
   continue.
2. `field.value in (None, "", [])` → review `missing_value`, continue.
3. `min(b.confidence for b in bindings) < 0.5` → append review
   `widget_low_confidence` with details, then keep going.
4. **Radio group** (`field.field_type == "radio_group" and len(bindings) > 1`):
   - `chosen = _select_radio_binding(bindings, field.value)`. None → review
     `unsettable_widget` and continue.
   - **/AS-write order matters** (this is the radio fix):
     ```
     for b in bindings where b.widget_xref != chosen.widget_xref:
         widget = find_widget(page, b.widget_xref)
         widget.field_value = "Off"   # explicit /AS=/Off on sibling
         widget.update()
     widget = find_widget(page, chosen.widget_xref)
     widget.field_value = chosen.on_value or True
     widget.update()
     ```
     Setting siblings first ensures the parent /V ends at the chosen on-value
     and every sibling's /AS is written explicitly. Without this, viewers see
     stale /AS on non-chosen siblings and render no dot. Errors → review
     `unsettable_widget`.
5. **Single-binding field** (`bindings[0]`):
   - Find widget by xref on `b.label_page - 1`. Missing → review
     `widget_unmapped` ("widget xref not found at write time").
   - `field_type == "checkbox"`: if `_checkbox_is_selected(value, label)`
     → `widget.field_value = b.on_value or True; widget.update()`. Skip if not
     selected (leave widget Off).
   - `field_type == "radio_group"` with single binding (degenerate) /
     `field_type == "signature"` / text-ish → `widget.field_value = str(value); widget.update()`.

After all fields: `doc.save(output_path, incremental=False, deflate=True)`.
Only `flatten=True` calls `doc.bake()` (omit by default — keeps PDF editable).

Helpers:
```python
_find_widget(page, xref):      iterate page.widgets() and match w.xref == xref
_set_text(widget, value):      widget.field_value = str(value or ""); update(); True on ok
_set_button_on(widget, on):    widget.field_value = on if on else True; update(); True on ok
_norm(s):                      (s or "").strip().lower()
_checkbox_is_selected(value, label):
    bool → value; list → True if non-empty AND any norm(v) == norm(label) (or just non-empty);
    str → strip non-empty; None → False
_select_radio_binding(bindings, value):
    target = norm(value) if str; None if not
    1) exact match on option_label or on_value
    2) substring either way on option_label
    None if no match
```

### `claims_parser/review_models.py` (added reasons)

```python
ReviewReason = Literal[
    # existing:
    "missing_value", "unanchored", "uncertain_after_vision",
    "large_residual_correction", "label_inference_failed",
    "missing_binding", "unsettable_widget",
    # added for this branch:
    "widget_unmapped",                # no binding for field
    "widget_low_confidence",          # min(binding.confidence) < 0.5
    "duplicate_label_match",          # (reserved; not currently emitted by writer)
    "radio_option_count_mismatch",    # (reserved)
]
```

## CLIs

### `extract.py` (reused, unchanged)

```
python extract.py <pdf> [-o <out.json>]
```
Default output: `output/intermediate/<pdf-stem>.context.json`

### `widget_extract.py`

```
python widget_extract.py <pdf> [-o <out.json>]
```
Default output: `output/intermediate/mapped/<pdf-stem>.widgets.json`. Reports
widget count by type.

### `map_widgets.py`

```
python map_widgets.py <pdf> <context.json> <widgets.json>
                       [-m <model>] [--mapping-out <p>] [--schema-out <p>]
```
Defaults:
- `--mapping-out` → `output/intermediate/mapped/<stem>.mapping.json`
- `--schema-out`  → `output/intermediate/mapped/<stem>.schema.json`
- `-m`           → `gpt-5-mini`

Calls `map_widgets()` then `build_schema_from_mapping()`, saves both. Prints
`bindings / unmapped / chunks` and `fields`.

### `fill_schema.py` (reused — Agent 2 CLI)

```
python fill_schema.py <schema.json> [-o <filled.json>] [-m <model>]
```

### `mapped_write.py`

```
python mapped_write.py <pdf> <filled.json>
                       [-o <out.pdf>] [--review-out <p>] [--flatten]
```
Defaults:
- `-o`           → `output/final/mapped/<pdf-stem>.filled.pdf`
- `--review-out` → `output/intermediate/mapped/<pdf-stem>.review.json`
- `--flatten`    → off (PDF remains editable)

## Generic-form contract

Nothing in this branch hardcodes form-specific names. Verified across:

- **Anthem NV CAID** (47 widgets: 25 text + 22 checkboxes, 2 pages)
- **CGS Medicare 56900 Reopening** (19 widgets: 15 text + 4 radio, 1 page);
  re-run with the radio value swapped to a different option still produces a
  correctly-marked, editable PDF.

The branch supports any healthcare claims appeal AcroForm PDF whose widgets
have legible printed labels visible to a vision model. No domain vocabulary,
issuer name, or field convention is encoded anywhere.
