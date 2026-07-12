# Healthcare Claims Appeal Form Filling Architecture

## 1. Purpose

This document defines the target architecture for an automated healthcare provider form-filling pipeline. The system receives an editable AcroForm PDF template and source claim/payment data, understands the PDF widgets and surrounding layout, maps each widget to a canonical healthcare field, resolves that field to the correct source JSON value, validates the mapping, and fills the PDF programmatically.

The architecture is designed for forms from healthcare payers and plans such as Aetna, Molina, Anthem, UHC, LA Care Health Plan, and similar organizations. The source payload is primarily derived from 835-style data, but the architecture does not assume a fixed source JSON schema because new fields and paths may be added over time.

The core design principle is evidence-first mapping. GPT-5.4-mini and memory are used to select and verify candidates, not to invent mappings from scratch.

## 2. Technology Stack

- PDF widget extraction and filling: PyMuPDF
- OCR and document layout extraction: Azure Document Intelligence prebuilt layout model
- LLM mapper, resolver, and verifier: GPT-5.4-mini
- Data models and structured LLM outputs: Pydantic
- Template fingerprinting: SHA-256 hash over stable form/layout signals
- Memory store: domain memory, payer-family memory, fingerprint memory, similar-template memory, and negative memory
- Evaluation corpus: editable AcroForms, source JSON examples, gold schema annotations, gold fillmaps, and filled PDF outputs

## 3. Architectural Goals

1. Improve precision by requiring local form evidence, source-data evidence, validation, and confidence calibration before filling a field.
2. Improve recall by generating deterministic candidates before invoking GPT-5.4-mini and by running a recall-recovery pass for skipped fields.
3. Improve generalization to new forms by reducing overreliance on exact fingerprint memory.
4. Preserve strong performance on known forms by reusing validated fingerprint memory and cached schemas.
5. Make every filled, skipped, and rejected field auditable.
6. Support source JSON evolution without hard-binding the system to a fixed schema.
7. Separate high-risk healthcare identifiers from low-risk optional fields using field-specific thresholds.

## 4. Main Data Contracts

All major stage outputs should be represented as Pydantic models. GPT-5.4-mini calls should return structured outputs that validate against these models.

### 4.1 Request Payload

```json
{
  "request_id": "string",
  "template_blob_url": "string",
  "source_data": {},
  "metadata": {
    "payer": "string",
    "form_name": "string",
    "tenant_id": "string",
    "workflow_type": "appeal|grievance|dispute|reconsideration",
    "claim_context": {}
  }
}
```

### 4.2 Widget JSON

Each AcroForm widget extracted by PyMuPDF should include:

- widget ID
- PDF xref
- page number
- widget type
- field name
- field label or alternate name if available
- bounding box
- choice values for dropdown/list widgets
- valid on-state values for checkboxes/radios
- max length
- flags
- default value
- current value
- parent-child relationships
- duplicate/repeated field group information

### 4.3 Layout Context JSON

Azure Document Intelligence layout output should be normalized into:

- pages
- page dimensions
- lines
- words
- word OCR confidence
- paragraphs
- paragraph roles
- tables
- cells
- row and column headers
- selection marks
- bounding polygons
- page headers and footers
- section headings
- markdown output if useful

### 4.4 Normalized Schema JSON

The schema JSON is the canonical understanding of the PDF template.

```json
{
  "fingerprint": "sha256",
  "schema_version": 1,
  "fields": [
    {
      "field_id": "provider.rendering.npi",
      "widget_id": "w_001",
      "widget_xref": 123,
      "widget_type": "text",
      "page": 1,
      "bbox": [0, 0, 100, 20],
      "label_name": "Rendering Provider NPI",
      "original_ocr_label": "Rendering Provider NPI",
      "tu_label": "rendering provider npi",
      "section_heading": "Provider Information",
      "ontology_match": "provider.rendering.npi",
      "allowed_value_type": "npi",
      "validators": ["npi_10_digits"],
      "candidate_evidence": [],
      "risk_flags": [],
      "mapper_confidence": 0.0
    }
  ]
}
```

### 4.5 Flattened Source Entry

The flattened source JSON must preserve context, not just path and value.

```json
{
  "path": "$.claims[0].provider.rendering.npi",
  "key": "npi",
  "parent_path": "$.claims[0].provider.rendering",
  "sibling_keys": ["name", "tin", "address"],
  "raw_value": "1234567893",
  "normalized_value": "1234567893",
  "value_type": "npi",
  "semantic_hints": ["provider", "rendering", "npi"],
  "array_context": {
    "claim_index": 0,
    "service_line_index": null
  },
  "source_provenance": "835"
}
```

### 4.6 Fillmap JSON

The fillmap is the final mapping between schema fields and source values.

```json
{
  "fields": [
    {
      "field_id": "provider.rendering.npi",
      "widget_id": "w_001",
      "widget_xref": 123,
      "source_path": "$.claims[0].provider.rendering.npi",
      "raw_source_value": "1234567893",
      "fill_value": "1234567893",
      "formatter": "npi_text",
      "resolver_confidence": 0.0,
      "final_confidence": 0.0,
      "source_evidence": [],
      "validation_results": [],
      "ambiguity_flags": [],
      "alternatives": [],
      "decision": "fill|abstain|review",
      "abstain_reason": null
    }
  ]
}
```

## 5. Memory Architecture

Memory should be separated by scope. The mapper, resolver, and verifier should not all receive the same broad memory context because this can create correlated mistakes.

### 5.1 Fingerprint Memory

Fingerprint memory stores exact form-specific knowledge.

Use it for:

- exact known form behavior
- validated widget-to-field mapping
- known source-field choices for a specific form
- historical corrections for the same fingerprint

Apply it only on exact or validated near-exact template matches.

### 5.2 Similar-Template Memory

Similar-template memory stores behavior from forms that are layout-similar but not exact matches.

Use it only when template novelty is low. Its influence should be weaker than exact fingerprint memory and should always require local evidence.

### 5.3 Payer-Family Memory

Payer-family memory stores conventions for specific payers or plan families.

Examples:

- payer-specific label vocabulary
- common field names
- common sections
- common checkbox language
- known payer-specific form variants

Apply only when payer or form-family detection is strong.

### 5.4 Domain Ontology Memory

Domain ontology memory stores general healthcare concepts.

Examples:

- member ID aliases
- subscriber/member/patient distinctions
- billing/rendering/facility/pay-to provider distinctions
- claim/control/reference number distinctions
- NPI/TIN/EIN distinctions
- date and amount field classes

This memory can be used for new forms.

### 5.5 Layout Pattern Memory

Layout pattern memory stores generalized layout rules.

Examples:

- label usually appears left of text field
- checkbox option text usually appears to the right of the checkbox
- table headers may define repeated field semantics
- section headings constrain nearby fields

This memory can be used for new forms, but only as weak evidence.

### 5.6 Negative Memory

Negative memory stores common wrong mappings and should be used aggressively by mapper, resolver, and verifier.

Examples:

- do not map provider NPI to TIN
- do not map member ID to claim number
- do not collapse subscriber, patient, and member unless local evidence supports it
- do not map billing provider to rendering provider unless the form explicitly asks for the same entity
- do not map service date to claim received date
- do not map payer claim control number to provider account number

Negative memory is especially important for new-template precision.

## 6. Template Novelty Policy

Before applying memory, compute a template novelty score.

Signals:

- exact fingerprint match
- page count similarity
- page dimension similarity
- widget count similarity
- widget type distribution similarity
- widget field-name similarity
- widget bbox/layout similarity
- section heading similarity
- payer/logo/form-title similarity
- OCR text similarity
- table structure similarity

Memory policy:

| Novelty state | Memory behavior |
| --- | --- |
| Exact fingerprint | Use fingerprint memory strongly after cheap validation |
| Low novelty | Use similar-template memory and payer-family memory, but require local evidence |
| Medium novelty | Use payer-family memory, domain ontology, layout pattern memory, and negative memory |
| High novelty | Use only domain ontology, local evidence, layout pattern memory as weak evidence, and negative memory |

## 7. End-to-End Processing Flow

### Step 1. Receive Request

Receive the source JSON, template blob URL, and request metadata.

Required metadata should include request ID and tenant/customer context. Optional metadata should include payer, form name, workflow type, provider context, and claim context.

### Step 2. Download Template

Download the empty editable AcroForm PDF from blob storage.

Persist a request-scoped copy for processing and audit.

### Step 3. Extract Widgets With PyMuPDF

Use PyMuPDF to extract all fillable widgets and create widget JSON.

Capture widget ID, xref, page, type, field name, label/alternate name, bbox, choices, on-state values, flags, default values, current values, max length, and parent-child relationships.

### Step 4. Compute Fingerprints

Compute a SHA-256 fingerprint over stable form signals. This is a hash/cache key, not encryption.

Include:

- page count
- page dimensions
- widget count
- widget field names
- widget xrefs where stable
- widget bboxes
- widget types
- choice/on-state metadata

Also compute secondary similarity hashes for near-duplicate detection.

### Step 5. Load Cache And Memory Candidates

Look up:

- exact fingerprint schema
- fingerprint memory
- similar-template memory
- payer-family memory
- domain ontology
- layout pattern memory
- negative memory

### Step 6. Compute Template Novelty

Compute template novelty before deciding how much memory to trust.

The novelty score controls which memory scopes are allowed and how heavily they may influence the mapper, resolver, and verifier.

### Step 7. Validate Exact Cache Hit

If the fingerprint is known, run cheap validation before trusting cached schema:

- page count matches
- widget count matches
- field type distribution matches
- xrefs or widget identities are stable
- bbox checksum is close
- schema version is compatible
- form family/payer metadata does not conflict

If validation passes, load the cached schema and skip expensive OCR and mapper steps.

If validation fails, continue through OCR and remapping.

### Step 8. Run Azure Document Intelligence For New Or Changed Forms

For new, novel, or invalidated templates, call Azure Document Intelligence prebuilt layout model.

Extract pages, words, lines, paragraphs, tables, selection marks, bounding polygons, headers, footers, and section headings.

### Step 9. Normalize Coordinate Systems

Normalize PyMuPDF widget coordinates and Azure layout coordinates into a common page coordinate system.

This is required for reliable nearest-label, same-row, table-cell, and section-heading reasoning.

### Step 10. Generate Widget-Label Candidates Deterministically

For each widget, generate candidate labels using geometry and layout structure before invoking GPT-5.4-mini.

Candidate sources:

- nearest text to the left
- nearest text above
- same-row text
- same-column text
- table header
- row header
- section heading
- page heading
- checkbox/radio option text
- nearby instruction/help text
- widget field name
- widget alternate name/tooltip
- historical labels from allowed memory

### Step 11. Score Widget-Label Candidates

Score candidates using deterministic features:

- distance from widget
- relation type: left, above, same row, same table, same section
- OCR confidence
- text grouping quality
- table structure support
- label-like text pattern
- ontology alias match
- negative alias conflict
- payer-family support
- exact or similar-template support
- conflict with other nearby widgets

### Step 12. Use GPT-5.4-mini Mapper As Reranker

The mapper LLM should select or rerank candidates. It should not generate labels freely unless all candidates are inadequate and the output is explicitly marked low-confidence.

Mapper input:

- widget metadata
- compact local OCR evidence
- top deterministic label candidates
- domain ontology snippets
- top relevant memory snippets allowed by novelty policy
- negative memory

Mapper output must validate against a Pydantic structured output model containing:

- widget ID
- selected label
- selected candidate source
- standardized field ID
- evidence text
- evidence relation
- mapper confidence
- ambiguity flags
- alternative candidates
- abstain/review reason if necessary

### Step 13. Normalize Through Healthcare Ontology

Map each selected label to a canonical healthcare field ID.

The ontology should define:

- standardized field IDs
- aliases
- payer-specific aliases
- expected value type
- allowed widget types
- required/optional status
- validators
- formatting rules
- negative aliases
- common wrong mappings
- entity distinctions

Critical entity distinctions:

- billing provider
- rendering provider
- facility provider
- pay-to provider
- member
- subscriber
- patient
- claim number
- payer claim control number
- provider account number
- service date
- received date

### Step 14. Create Normalized Schema JSON

Clean mapper output into schema JSON.

Each schema field should include standardized field ID, widget ID, xref, widget type, bbox, selected label, original OCR label, normalized label, section heading, page, evidence, ontology match, allowed value type, validators, risk flags, mapper confidence, and schema version.

### Step 15. Validate And Store Schema

Store schema under the fingerprint folder only if it passes validation.

Persist:

- fingerprint
- template name
- payer/family
- first-seen timestamp
- schema version
- widget count
- page count
- field hash
- OCR context hash
- last eval score
- human correction status
- model and prompt versions used to create it

### Step 16. Flatten And Normalize Source JSON

Flatten the source JSON while preserving context.

For each source entry, store:

- JSON path
- key name
- parent path
- sibling keys
- raw value
- normalized value
- value type
- semantic hints
- claim/service-line/provider array context
- provenance from original source

The flattened representation must support new source paths without requiring a fixed source schema.

### Step 17. Generate Source Candidates

For every schema field, generate candidate source paths before invoking GPT-5.4-mini.

Candidate generation should use:

- ontology aliases
- JSON key similarity
- parent-path similarity
- sibling-key context
- payer/domain hints
- value type compatibility
- regex/validator compatibility
- source value presence
- allowed memory under novelty policy
- negative memory exclusions

### Step 18. Score Source Candidates

Score source candidates deterministically:

- lexical similarity
- semantic alias match
- path/entity match
- type match
- validator pass/fail
- source value presence
- negative memory conflict
- conflict with already-used source fields
- payer/fingerprint/similar-template memory support
- consistency with other fields on the same form section

### Step 19. Use GPT-5.4-mini Resolver As Candidate Selector

The resolver LLM should select among shortlisted source candidates.

Resolver input:

- normalized schema field
- top source candidates
- source candidate evidence
- ontology definition
- validator results
- relevant field-level memory
- negative memory

The resolver should not receive broad raw layout memory. Layout memory belongs mainly to the mapper stage.

Resolver output must validate against a Pydantic structured output model containing:

- field ID
- widget ID
- selected source JSON path
- raw source value
- normalized fill value
- formatter
- resolver confidence
- evidence
- validation results
- ambiguity flags
- alternatives
- decision
- abstain reason if not mapped

### Step 20. Apply Healthcare Validators And Blockers

Apply deterministic validation before final acceptance.

Required validators and blockers:

- NPI must be 10 digits
- TIN/EIN/SSN-like fields must not be confused
- dates must parse and format correctly
- currency fields must preserve cents and expected formatting
- phone/fax fields must normalize correctly
- ZIP/address fields must preserve correct components
- checkbox/radio values must use allowed on-state values
- dropdown/list values must match allowed choices
- member/subscriber/patient fields must be distinguished
- billing/rendering/facility/pay-to provider fields must be distinguished
- service date, received date, and appeal date must be distinguished
- payer claim control number and provider account number must be distinguished

### Step 21. Compute Calibrated Confidence

Do not rely only on an LLM confidence score.

Compute final confidence from:

- mapper confidence
- resolver confidence
- local label evidence strength
- source candidate strength
- OCR confidence
- distance/geometric relation
- ontology match strength
- negative-memory penalty
- validator pass/fail
- historical cache performance
- template novelty score
- field criticality
- conflict with related fields

### Step 22. Apply Field-Specific Thresholds

Use different confidence thresholds by field class.

Higher thresholds:

- NPI
- TIN/EIN
- member ID
- claim number
- provider identity
- legal attestations
- high-impact checkboxes/radios
- appeal/dispute reason selections

Lower thresholds:

- phone
- fax
- optional contact name
- address line 2
- clearly labeled optional administrative fields

### Step 23. Run Evidence-Based Verifier

The final judge/filter LLM should act as an evidence verifier, not as a third mapper.

Verifier input:

- proposed mapping
- local label evidence
- source path evidence
- validator results
- confidence components
- ambiguity flags
- negative memory

The verifier should not receive the same broad memory context as the mapper. This reduces correlated mistakes.

Verifier output:

- accept
- reject
- abstain
- require human review
- reason code
- concise evidence summary
- risk flags

### Step 24. Require Evidence Records

Every field must produce an evidence record whether it is filled, skipped, rejected, or routed to review.

Evidence record fields:

- field ID
- widget ID
- source path
- fill value
- local label evidence
- label relation
- source key/path evidence
- validator result
- memory support
- risk flags
- final confidence
- final decision
- reason code

### Step 25. High-Precision Fill Pass

Fill only fields that:

- pass validators
- meet field-specific confidence thresholds
- have strong local label evidence
- have strong source evidence
- have no unresolved high-risk flags
- pass evidence-based verification

### Step 26. Recall-Recovery Pass

For unfilled expected fields, run a second pass focused only on missed fields.

Classify the skip reason:

- source value missing
- no widget candidate
- weak widget label evidence
- weak source candidate evidence
- validator failed
- confidence below threshold
- ambiguous entity
- unsupported widget type
- memory conflict
- verifier rejected

In recall recovery, allow lower thresholds only for low-risk fields. High-risk identifiers and legal/clinical checkboxes should remain conservative.

### Step 27. Finalize Fillmap

Finalize the fillmap with:

- accepted fills
- abstained fields
- review-required fields
- rejected candidate mappings
- reason codes
- confidence breakdown
- evidence records

### Step 28. Fill PDF With PyMuPDF

Use PyMuPDF to fill the PDF programmatically.

Enforce widget-type rules:

- text widgets receive normalized text
- checkbox widgets receive valid on-state values
- radio widgets receive valid on-state values
- dropdown/list widgets receive allowed choices
- unsupported widgets are skipped or routed to review

After setting values, update widget appearances as required by PyMuPDF behavior.

### Step 29. Re-Open And Verify Filled PDF

Re-open the output PDF and extract filled widget values.

Compare extracted values against final fillmap values to catch:

- failed writes
- checkbox/radio state errors
- unsupported option values
- truncation
- formatting problems
- invisible appearance issues
- widget value normalization issues

### Step 30. Generate Audit Artifact

Create a request-level audit artifact containing:

- request ID
- input template fingerprint
- source JSON hash/version
- schema version
- memory policy used
- model versions
- prompt versions
- accepted fills
- skipped fields
- review fields
- rejected mappings
- confidence scores
- evidence records
- validation results
- cache hit/miss status
- template novelty score
- final PDF verification results

### Step 31. Store Corrections And Feedback

Human corrections and production feedback should update the correct memory scope.

Update fingerprint memory when the correction is exact-template-specific.

Update payer-family memory when the correction repeats across payer forms.

Update domain ontology when the correction is generally true.

Update negative memory when the correction prevents a future false positive.

Update the eval regression set for every meaningful correction.

## 8. Evaluation Architecture

Evaluation should be stage-specific and should not rely only on visual inspection of final PDFs.

### 8.1 Gold Artifacts

Maintain gold data at three levels:

1. Template schema gold: widget ID or xref to human label, standardized field ID, bbox, and widget type.
2. Fillmap gold: standardized field ID to source JSON path and expected normalized value.
3. Final PDF gold: extracted filled widget values compared against expected values.

### 8.2 Evaluation Buckets

Measure performance separately for:

- exact fingerprint cache hit
- near-duplicate template
- new template, known payer
- new payer or new form family
- changed source JSON schema
- missing source data
- ambiguous fields
- checkbox/radio fields
- dropdown/list fields
- high-risk identifier fields
- optional administrative fields

### 8.3 Core Metrics

Track:

- widget extraction recall
- label mapping precision
- label mapping recall
- schema normalization accuracy
- source resolution precision
- source resolution recall
- final fill precision
- final fill recall
- false-fill rate
- abstention accuracy
- review routing accuracy
- value normalization accuracy
- checkbox/radio accuracy
- dropdown/list accuracy
- final PDF write success rate

### 8.4 Failure Attribution

Every failed or missed field should be attributed to a stage:

- widget extraction failure
- OCR/layout failure
- coordinate normalization failure
- label candidate generation failure
- mapper failure
- schema normalization failure
- source flattening failure
- source candidate generation failure
- resolver failure
- validator/filter failure
- verifier failure
- PyMuPDF fill/write failure
- final PDF verification failure

### 8.5 Memory Ablation

Run ablation evals with:

- no memory
- domain ontology only
- domain ontology plus negative memory
- domain plus payer-family memory
- domain plus similar-template memory
- exact fingerprint memory

Use these results to tune memory weights and novelty thresholds. If memory improves recall but hurts precision on new forms, reduce memory influence for high-novelty templates and strengthen local evidence requirements.

## 9. Precision And Recall Strategy

### 9.1 Precision Controls

Precision is protected by:

- deterministic candidate generation
- local form evidence requirements
- source evidence requirements
- negative memory
- ontology validators
- field-specific confidence thresholds
- evidence-based verifier
- high-precision first pass
- final PDF write verification

### 9.2 Recall Controls

Recall is improved by:

- geometry-based candidate generation
- table and section-aware layout parsing
- source candidate generation from rich flattened JSON
- ontology aliases
- payer-family memory
- similar-template memory when novelty is low
- recall-recovery pass
- human correction feedback loop

### 9.3 Avoiding Memory Overreach

Memory should not override weak local evidence on new forms.

For high-novelty forms:

- prioritize OCR/widget evidence
- use domain ontology
- use negative memory
- use layout pattern memory only weakly
- avoid broad fingerprint or similar-template memory unless similarity is proven

## 10. Deployment And Observability

Each production request should log:

- request ID
- template fingerprint
- novelty score
- cache hit/miss
- memory scopes used
- number of widgets
- number of schema fields
- number of accepted fills
- number of abstentions
- number of review-required fields
- false-fill risk flags
- model versions
- prompt versions
- latency by stage
- Azure Document Intelligence cost/latency
- GPT-5.4-mini token usage by mapper/resolver/verifier
- PDF fill verification result

Dashboards should report precision, recall, false-fill rate, abstention rate, review rate, and final PDF write success rate by payer, form family, field class, novelty bucket, and memory policy.

## 11. Acceptance Criteria

The updated pipeline should be considered successful only if it improves new-template behavior without degrading known-template behavior.

Minimum acceptance criteria:

1. Exact fingerprint cache-hit performance remains stable or improves.
2. New-template precision improves versus the current baseline.
3. New-template recall improves or recall-recovery identifies actionable skip reasons.
4. False-fill rate decreases for high-risk identifiers.
5. Every filled field has a complete evidence record.
6. Every skipped expected field has a reason code.
7. Final PDF verification catches failed writes and widget-state errors.
8. Memory ablation demonstrates which memory scopes help and which hurt.

## 12. Summary

The target architecture keeps the useful parts of the current pipeline: PyMuPDF widget extraction, Azure Document Intelligence layout OCR, GPT-5.4-mini mapper/resolver/verifier, schema caching by fingerprint, and memory-assisted mapping.

The main changes are:

- use deterministic candidate generation before GPT-5.4-mini
- use GPT-5.4-mini as a selector and verifier rather than a free-form mapper
- separate memory by scope and gate it by template novelty
- add negative memory
- introduce a canonical healthcare field ontology
- preserve rich context when flattening source JSON
- compute calibrated confidence instead of relying only on LLM judgment
- apply field-specific thresholds
- run high-precision and recall-recovery passes
- verify the final PDF after filling
- evaluate every stage independently

This design should improve generalization to new forms because decisions are anchored in local layout evidence, source-data evidence, ontology constraints, validators, and calibrated confidence rather than broad memory or open-ended LLM inference.
