# Known Issues

Findings parked for later. Each item is self-contained — pick one up without re-running the original demo.

---

## 1. Checkbox groups with shared label collapse incorrectly

**Discovered:** 2026-05-31, running `notebooks/mapped_acroform_demo.ipynb` on `input/Aetna_Form.pdf` and `input/AETNA_form2.pdf`.

**Symptom:** Both Plan Type checkboxes on each Aetna form end up ticked in the output PDF (Medical *and* Dental). The review report shows 0 items — the writer thinks everything succeeded.

**Root cause:** Schema builder collapses radio groups but not checkbox groups.

The vision mapper does its job correctly: in both Aetna forms it emits two distinct bindings sharing one `semantic_field_id="plan_type"`, with separate `option_label` values (`"Medical"`, `"Dental"`):

```
xref=142 CB.3a  label='Plan Type'  option_label='Medical'   semantic_id='plan_type'
xref=144 CB.3b  label='Plan Type'  option_label='Dental'    semantic_id='plan_type'
```

Then in [notebooks/mapped_acroform_demo.ipynb](../notebooks/mapped_acroform_demo.ipynb) cell 16 (and the canonical [claims_parser/widget_mapper.py](../claims_parser/widget_mapper.py) `build_schema_from_mapping`), the grouping logic only collapses radios:

```python
if types == {"radio"} and len(group) > 1:
    # build one radio_group FormField with options = [option_label for b in group]
```

Checkbox groups fall through to the per-widget branch, where `_type_and_options` sets `options=[b.label_text]` — i.e. `["Plan Type"]`, **not** `["Medical", "Dental"]`. Agent 2 then sees two fields each labeled "Plan Type" with one allowed option ("Plan Type") and selects it on both. The writer ticks each independently.

**Fixes (any subset; (a) is the minimum to make the bug go away):**

(a) **Schema builder** — also collapse checkbox groups whose bindings share a `semantic_field_id` *and* expose distinct, non-null `option_label`s. Emit one `checkbox` FormField with `options=[<option labels…>]`. Agent 2's prompt already says checkbox values are a list drawn from `options`, so it will pick a real subset (e.g. `["Medical"]`) instead of echoing the group label.

  Apply to **both**:
  - [notebooks/mapped_acroform_demo.ipynb](../notebooks/mapped_acroform_demo.ipynb) cell 16 (`build_schema_from_mapping`)
  - [claims_parser/widget_mapper.py](../claims_parser/widget_mapper.py) `build_schema_from_mapping`

  Sketch:
  ```python
  if types == {"radio"} and len(group) > 1:
      # existing radio_group branch
      ...
  elif types == {"checkbox"} and len(group) > 1 and all(b.option_label for b in group):
      label = group[0].label_text
      options = [b.option_label for b in group]
      fields.append(FormField(
          field_id=_uniqify(_snake(raw_id), taken),
          label=label, section=None,
          field_type="checkbox", options=options,
          format_hint=None, required=False,
          source_text=label, widget_bindings=group,
      ))
  else:
      # existing per-widget branch
      ...
  ```

  Writer change needed: when a `checkbox` field has multiple `widget_bindings`, treat its `value` (a list of option labels from Agent 2) the same way the radio branch already does — write each selected sibling's `on_value`, write `"Off"` to unselected siblings. See the radio block in [claims_parser/mapped_writer.py:117-150](../claims_parser/mapped_writer.py) for the pattern.

(b) **Defensive review flag** — emit `widget_low_confidence` (or a new reason) when a checkbox field's filled value equals or contains its own label. That symptom is almost always (a). Lives in `mapped_writer.py` next to the existing confidence check, around line 110.

(c) **Mirror-test the fix** — re-run the notebook on `Aetna_Form.pdf` (or `AETNA_form2.pdf`); expect exactly one of Medical/Dental ticked in the output PDF, and a `checkbox` field in `filled.json` whose `value` is a singleton list drawn from `options`.

**Evidence on disk (do not delete until verified):**
- `output/Aetna_Form.{widgets,mapping,schema,filled,review,filled.pdf}.json` / `.pdf`
- `output/AETNA_form2.{widgets,mapping,schema,filled,review,filled.pdf}.json` / `.pdf`
- `mappings/fc340c10149f85f3….{cached,mapping,schema}.json` (Aetna_Form)
- `mappings/b15cf71b7bb5ef14….{cached,mapping,schema}.json` (AETNA_form2)

**Why ANTHEM didn't expose this:** its mutually-exclusive groups (Provider Type, Appeal Level, etc.) are modeled as PDF radios. Aetna uses checkboxes for the same semantics, so the radio-only collapse silently mis-handles them.
