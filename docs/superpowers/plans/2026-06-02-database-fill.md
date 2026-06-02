# Database-driven Fill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Do not start until the user explicitly asks for implementation** — this plan is being created as a reference document while the user iterates on the source-of-truth JSON.

**Goal:** Replace Agent 2 (LLM filler) with a deterministic, database-driven filler that maps a fixed source-of-truth DB JSON to each `FormSchema`, with LLM assistance used only once per form (cached) for the resolution step. The downstream writers (non-editable, AcroForm direct, mapped AcroForm) remain unchanged.

**Architecture:** Two new modules — `db_mapper.py` (slow, LLM-assisted, cacheable: produces a per-form `FieldMap` artifact) and `db_filler.py` (fast, deterministic, no network: applies the `FieldMap` to a DB JSON to produce `FilledFormSchema`). One CLI flag (`--db-json`) added to the existing `fill_schema.py` so all three branches consume the change at one junction. The LLM filler stays in tree as a fallback.

**Tech Stack:** Python 3, pydantic v2, openai (existing), hashlib (stdlib SHA-256), argparse. No new third-party deps.

**Source-of-truth DB JSON:** `.claude/worktrees/mapping-cache/database_schema.json` (sample). Schema is **fixed** across all forms and cases — only values and `claim.serviceLines` cardinality vary.

**Reference design:** [docs/database-fill-design.md](../../database-fill-design.md)

**LLM role (revised):** The LLM that previously filled per-form dummy data ([claims_parser/form_filler.py](../../../claims_parser/form_filler.py)) is repurposed to fill the **canonical DB JSON template** instead. One LLM call per case, regardless of how many forms that case fills. This:
- Decouples "generate test data" from "fill a form" — the same filled DB JSON drives 1 or 100 different forms identically.
- Replaces per-form Agent-2 calls with a single fixed-schema fill (the DB schema never changes, so prompt + pydantic response model are stable).
- Keeps Agent 2's infrastructure (`openai`, structured outputs, `gpt-5-mini`) in use without form-specific knowledge.
- In production: skip the template-fill step; feed the real DB JSON directly to the mapper+filler.

---

## Why this design (LLM vs functional vs hybrid)

The user explicitly asked the three approaches to be weighed. Decomposing by sub-task:

| Sub-task | LLM | Pure functional | Winner |
|---|---|---|---|
| Decide which DB block a form field draws from (`"Patient Last Name"` → `patient.lastName`) | Robust to label phrasing variations across forms | Hand-mapped per form, or label-regex heuristics | **LLM** |
| Compose `firstName + middleName + lastName` for a single "Patient Name" box | Trivial via template | Manual rule per form | **LLM** for picking the rule (once); functional for applying it |
| Detect a service-line **table** on the form and assign each cell to `serviceLines[N]` | Reasoning over `field_id` patterns + section context | Brittle suffix-regex (`_row_1`, `(1)`, etc.) | **LLM** (with a functional fast-path for obvious patterns) |
| Match `radio_group` options ("Yes"/"No" ↔ `relationshipCode == "18"`) | Strong with reasoning | Pattern table; weak for novel options | **LLM** |
| Disambiguate among similar amounts (`paidAmount`, `claim.claimPaidAmount`, `submittedAmount`, `claim.claimSubmittedCharges`) | Strong with form context | Cannot disambiguate at all | **LLM** |
| Date / phone / number / currency format coercion | Wasteful and non-deterministic | Pure transforms keyed off `format_hint` | **Functional** |
| Apply the chosen mapping to the actual data | n/a | Pure dictionary lookup | **Functional** |
| Treat empty strings, missing keys, list-out-of-bounds | n/a | Trivial guards | **Functional** |

The natural split is: **LLM for resolution (form → DB-path rules), functional for materialization (rules + data → values)**. This mirrors the existing mapped-AcroForm branch (vision LLM once for widget→label binding, deterministic apply afterward) — same architectural shape, same caching pattern, same `output/intermediate/<stem>.fillmap.json` contract on disk.

**Why not LLM-only:**
- Slow on every fill (the same form filled for 500 patients = 500 LLM calls).
- Non-deterministic outputs would force the writers to absorb formatting drift.
- Token cost scales with case volume, not form variety.

**Why not functional-only:**
- DB JSON has ~150 distinct paths; form schemas have 50–200 fields each. The cartesian space cannot be hand-mapped without per-form authoring.
- Form labels paraphrase differently across insurers ("Subscriber Member ID", "Insured ID", "Subscriber #"). Without semantic matching, the mapper degrades to a manual lookup table.
- Cannot pick the right block when DB has overlapping data (`patient` vs `subscriber` vs `dependent` blocks all carry `firstName`/`lastName`).

**Hybrid (chosen): LLM-resolve once per form, deterministic apply forever after.**

---

## DB JSON structural facts (what the mapper must understand)

From [.claude/worktrees/mapping-cache/database_schema.json](../../../.claude/worktrees/mapping-cache/database_schema.json):

- **Envelope:** `{success, message, result, executionTimeSec}`. Real payload is `result.*`. The mapper operates on `result`; the loader strips the envelope.
- **Top-level scalars in `result`:** `appealInventoryId`, `claimStatusToken`, `workType`, `createdBy`.
- **Nested objects** (each is a flat dict — schema is fixed):
  - `claim` (~25 fields including `claim.appealMethod` sub-object with 6 nullable slots)
  - `patient` (10 fields)
  - `subscriber` (9 fields, often identical to `patient` when `relationshipCode == "18"` Self)
  - `dependent` (8 fields)
  - `payer` (~18 fields)
  - `provider` (~18 fields)
- **Variable cardinality:** `claim.serviceLines` is a list. Each item has ~14 scalar fields and a nested `serviceAdjustment` list.
- **Date formats inside the DB itself are inconsistent:**
  - `YYYYMMDD`: `claim.claimBeginningDateOfService = "20250606"`, `claim.appealsFilingDate = "20260513"`, all DOBs.
  - `MM/DD/YYYY`: `claim.claimServiceDateEnd = "01/23/2024"`, `claim.checkEftDate`, all `serviceLines[*].serviceDateStart`/`End`.
  - The materializer must auto-detect the input format from the value itself, then re-emit per the form's `format_hint`.
- **Empty strings** (`""`) are common — treat as missing.
- **Typos in source schema** (must be used as-is, the user said it's fixed):
  - `serviceLines[*].modeifier` (sic)
  - `serviceLines[*].claimAdjustementReasonCode` (sic at row level; the nested `serviceAdjustment[*].claimAdjustmentReasonCode` is spelled correctly)
- **Derived facts** the mapper / filler can produce:
  - "Patient is subscriber?" → `result.patient.relationshipCode == "18"`
  - Service line count → `len(result.claim.serviceLines)`
  - Combined name → `firstName + middleName + lastName`
  - Total charges → sum of `serviceLines[*].charges` (rarely needed; usually a top-level field exists)

---

## File map

| Path | Action | Responsibility |
|---|---|---|
| `claims_parser/db_template_models.py` | create | Pydantic model mirroring the canonical DB JSON shape (used by the LLM template-filler in Task 0) |
| `claims_parser/db_template_filler.py` | create | LLM (Agent 2 repurposed) fills empty values in the canonical DB JSON template |
| `fill_db.py` | create | Root CLI for the template-filler. Input: DB JSON template (possibly partial). Output: filled DB JSON |
| `claims_parser/db_mapping_models.py` | create | `Resolution`, `OptionResolution`, `FieldResolution`, `FieldMap`, `UnresolvedField` pydantic models |
| `claims_parser/db_loader.py` | create | Load + envelope-strip; flatten to dotted paths; `db_schema_fingerprint` over key set |
| `claims_parser/db_filler.py` | create | Apply `FieldMap` + DB → `FilledFormSchema`. Pure, deterministic, no network |
| `claims_parser/db_coercers.py` | create | Pure value coercers: dates, phones, numbers, currency, address compose, option resolve |
| `claims_parser/db_mapper.py` | create | Heuristic resolve → LLM resolve cascade; cache load/store |
| `tests/test_db_coercers.py` | create | Bare-assert unit tests for every coercer |
| `tests/test_db_filler.py` | create | TDD harness against hand-authored `FieldMap` + the real DB JSON |
| `tests/test_db_mapper_heuristic.py` | create | Tests for the no-LLM resolution fast-path |
| `map_db.py` | create | Root CLI: schema + DB → fillmap (writes `<stem>.fillmap.json`) |
| `fill_schema.py` | modify | Add `--db-json PATH` flag; when set, take the deterministic path; otherwise fall back to the LLM filler |
| `claims_parser/__init__.py` | modify | Export new public surface |
| `claims_parser/review_models.py` | modify | Add `unmapped_db_key` reason |
| `claims_parser/pdf_writer.py` | modify | When a field has no value, flag with `unmapped_db_key` if its `field_id` is in a sidecar set (or just keep `missing_value`) |
| `claims_parser/acroform_writer.py` | modify | Same review-reason update |
| `claims_parser/mapped_writer.py` | modify | Same review-reason update |
| `output/intermediate/<stem>.fillmap.json` | create at runtime | Per-form resolution cache (form-keyed, case-independent) |

The DB JSON itself lives outside the repo (one per case, gitignored — same posture as `input/` because it may contain PHI). Expected default location: `database/<case>.json` (see Task 11 for the `.gitignore` change).

---

## Resolution artifact — the contract that binds the mapper to the filler

The whole design pivots on this object. Get it right before any task touches it.

```python
# claims_parser/db_mapping_models.py

from typing import Literal, Optional
from pydantic import BaseModel, Field


class Resolution(BaseModel):
    """How to produce a value for a single FormField from the DB JSON."""

    kind: Literal["scalar", "compose", "service_line", "derived", "constant", "unmapped"]
    paths: list[str] = Field(
        default_factory=list,
        description=(
            "Dotted DB paths into result.*. Empty for kind=constant or unmapped. "
            "Multiple paths only for kind=compose."
        ),
    )
    template: Optional[str] = Field(
        default=None,
        description=(
            "Python str.format template for kind=compose. Positional placeholders {0}, {1}, ... "
            "reference `paths` in order. None for other kinds."
        ),
    )
    transform: Optional[str] = Field(
        default=None,
        description=(
            "Coercion spec applied to the raw value(s). "
            "Allowed: 'date:<out_format>', 'phone:digits', 'phone:formatted', 'number', "
            "'currency', 'upper', 'lower', 'strip'. None means pass-through string."
        ),
    )
    service_line_index: Optional[int] = Field(
        default=None,
        description="0-based index into claim.serviceLines for kind=service_line. None otherwise.",
    )
    derive: Optional[dict] = Field(
        default=None,
        description=(
            "For kind=derived: a tiny predicate spec, e.g. "
            "{'op': 'eq', 'path': 'patient.relationshipCode', 'value': '18', "
            "'true_value': 'Yes', 'false_value': 'No'}. "
            "Allowed ops: eq, neq, non_empty, empty. Nothing else — keep the surface small."
        ),
    )
    literal: Optional[str] = Field(
        default=None,
        description="For kind=constant: the literal value to emit. None otherwise.",
    )
    reason: str = Field(
        description="One-line explanation of why this resolution was chosen (or why unmapped). Human-readable."
    )


class OptionResolution(BaseModel):
    """For radio_group / checkbox FormFields: how to pick which option(s) match the DB."""
    option_label: str  # the form's verbatim option text
    when: Resolution   # the resolution that yields the matching condition. kind in {scalar, derived, constant, unmapped}
    selected_if: Literal["truthy", "equals", "always"] = "truthy"
    equals_value: Optional[str] = None   # used when selected_if == "equals"


class FieldResolution(BaseModel):
    field_id: str
    confidence: Literal["high", "medium", "low", "unmapped"]
    value: Resolution                 # for everything except radio_group/checkbox
    options: list[OptionResolution] = []  # ONLY for radio_group / checkbox; empty otherwise


class FieldMap(BaseModel):
    """Per-form resolution cache. One file per form; reused across cases."""
    schema_fingerprint: str           # hash of FormSchema field shapes (NOT values)
    db_schema_fingerprint: str        # hash of the DB JSON key set (NOT values)
    form_pdf_basename: str
    created_at: str                   # ISO-8601 UTC
    field_resolutions: list[FieldResolution]
    unresolved_field_ids: list[str] = []
    notes: list[str] = []             # mapper observations (e.g., "detected service-line table on page 2")
```

This shape is intentionally narrow. It admits exactly the patterns the DB JSON requires and nothing else. If a future requirement needs richer derivations, add a new `kind` — don't smuggle behavior into `transform` strings.

---

## Task 0: DB-template LLM filler (Agent 2 repurposed)

The canonical DB JSON ([`.claude/worktrees/mapping-cache/database_schema.json`](../../../.claude/worktrees/mapping-cache/database_schema.json)) defines a fixed schema. In production, real values arrive populated. In dev/testing, fill empty values with plausible dummy data using a single LLM call against this **fixed** schema. Output is the same file shape, fully populated, ready for Tasks 1+.

This is the load-bearing simplification: the prompt and pydantic response model never change because the DB schema never changes. Contrast with [claims_parser/form_filler.py](../../../claims_parser/form_filler.py) (the per-form Agent 2), which has to reason about arbitrary `FormSchema` shapes every call.

**Files:**
- Create: `claims_parser/db_template_models.py`
- Create: `claims_parser/db_template_filler.py`
- Create: `fill_db.py` (root CLI)
- Create: `tests/test_db_template_filler.py`

- [ ] **Step 1: Mirror the DB JSON shape as pydantic models**

```python
# claims_parser/db_template_models.py
"""Pydantic models mirroring the canonical DB JSON. Used as the response_format
for the LLM template-filler in db_template_filler.py.

The model shape is INTENTIONALLY EXACT. If the upstream DB schema changes,
this file changes — but no other module needs to know, because every
downstream consumer treats DB data as plain dicts via db_loader."""
from typing import Optional
from pydantic import BaseModel, Field


class ServiceAdjustment(BaseModel):
    totalAmount: str = ""
    claimAdjustmentReasonCode: str = ""
    claimAdjustmentDescription: str = ""
    remittanceAdviceRemarkCode: list[str] = []
    remittanceAdviceDescription: list[str] = []


class ServiceLine(BaseModel):
    serviceDateStart: str = ""
    serviceDateEnd: str = ""
    procedureCode: str = ""
    modeifier: str = ""  # typo preserved from source schema (sic)
    revenueCode: str = ""
    units: str = ""
    charges: str = ""
    allowedAmount: str = ""
    deductibleAmount: str = ""
    coinsuranceAmount: str = ""
    paidAmount: str = ""
    claimAdjustementReasonCode: str = ""  # typo preserved from source (sic)
    claimAdjustmentDescription: str = ""
    remittanceAdviceRemarkCode: str = ""
    remittanceAdviceDescription: str = ""
    serviceAdjustment: list[ServiceAdjustment] = []


class AppealMethod(BaseModel):
    appealMethod1: Optional[str] = None
    appealMethod2: Optional[str] = None
    appealMethod3: Optional[str] = None
    clinicalAppealMethod1: Optional[str] = None
    clinicalAppealMethod2: Optional[str] = None
    clinicalAppealMethod3: Optional[str] = None


class Claim(BaseModel):
    appealsFilingDate: str = ""
    claimAuthorizationNumber: str = ""
    claimBeginningDateOfService: str = ""
    claimEndDateOfService: str = ""
    claimNumber: str = ""
    claimPaidAmount: str = ""
    claimPatientResponsibility: str = ""
    claimReasonCodes: list[int] = []
    claimServiceDate: str = ""
    claimServiceDateEnd: str = ""
    claimServiceDateStart: str = ""
    claimStatusCode: str = ""
    claimSubmissionDate: str = ""
    claimSubmittedCharges: str = ""
    clearingHouseClaimNumber: str = ""
    cobIndicator: str = ""
    dateReceived: str = ""
    denialCategory: str = ""
    denialSummary: str = ""
    denialCategoryNextBestAction: str = ""
    documentControlNumber: str = ""
    effectiveDate: str = ""
    medicalRecordNumber: str = ""
    paidAmount: str = ""
    accountBalance: str = ""
    patientAccountNumber: str = ""
    patientControlNumber: str = ""
    payerClaimNumber: str = ""
    serviceLines: list[ServiceLine] = []
    serviceType: str = ""
    statementDateFrom: str = ""
    statementDateTo: str = ""
    submittedAmount: str = ""
    typeOfClaim: str = ""
    totalDeniedChargedAmount: str = ""
    checkEftDate: str = ""
    appealMethod: AppealMethod = AppealMethod()


class Person(BaseModel):
    dateOfBirth: str = ""
    firstName: str = ""
    gender: str = ""
    groupNumber: str = ""
    lastName: str = ""
    memberID: str = ""
    middleName: str = ""


class Patient(Person):
    patientType: str = ""
    relationshipCode: str = ""
    relationshipCodeDefinition: str = ""


class Dependent(Person):
    patientRelationship: str = ""


class Subscriber(Person):
    relationshipCode: str = ""
    relationshipCodeDefinition: str = ""


class Payer(BaseModel):
    email: str = ""
    fax: str = ""
    healthPlan: str = ""
    payerExchangeID: str = ""
    payerId: str = ""
    payerName: str = ""
    payerPlanID: str = ""
    phone: str = ""
    primarySubmission: str = ""
    secondarySubmission: str = ""
    payerAddress: str = ""
    payerCity: str = ""
    payerState: str = ""
    payerZip: str = ""
    payerWebsite: str = ""
    payerPolcityURL: str = ""  # typo preserved (sic)
    templateURL: str = ""
    formName: str = ""
    docType: str = ""
    docSize: str = ""
    templateFileName: str = ""


class Provider(BaseModel):
    clientName: str = ""
    clientID: str = ""
    clientAddress: str = ""
    billingProviderName: str = ""
    renderingProviderName: str = ""
    serviceProviderName: str = ""
    billingTin: str = ""
    billingNpi: str = ""
    billingMpin: str = ""
    billingTaxID: str = ""
    renderingTin: str = ""
    renderingNpi: str = ""
    renderingMpin: str = ""
    renderingTaxID: str = ""
    serviceProviderTin: str = ""
    serviceProviderNpi: str = ""
    serviceProviderMpin: str = ""
    serviceProviderTaxID: str = ""
    facilityName: str = ""
    facilityAddress: str = ""
    providerAddress: str = ""


class DBResult(BaseModel):
    appealInventoryId: str = ""
    claimStatusToken: str = ""
    workType: str = ""
    claim: Claim = Claim()
    dependent: Dependent = Dependent()
    patient: Patient = Patient()
    payer: Payer = Payer()
    provider: Provider = Provider()
    subscriber: Subscriber = Subscriber()
    createdBy: str = ""


class DBEnvelope(BaseModel):
    success: bool = True
    message: str = ""
    result: DBResult = DBResult()
    executionTimeSec: float = 0.0
```

- [ ] **Step 2: Write the LLM filler**

```python
# claims_parser/db_template_filler.py
"""Use gpt-5-mini to fill empty values in a canonical DB JSON template.

Single fixed prompt + fixed response_format means this prompt is the most
stable LLM surface in the project. The DB schema doesn't change form to form,
so the prompt doesn't either.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from openai import OpenAI

from claims_parser.db_template_models import DBEnvelope

DEFAULT_MODEL = "gpt-5-mini"

SYSTEM_PROMPT = """You receive a JSON document representing a healthcare claim
appeal record. Some fields are populated; others are empty strings, empty lists,
or null. Return the SAME document with every empty field filled with realistic,
internally-consistent fictional values.

Rules:
- Do not change any field that already has a non-empty value.
- Keep names of fields exactly as given (including any typos in source keys).
- Values must be consistent: patient name, DOB, addresses, IDs all belong to
  the same fictional individual; dates are mutually plausible; amounts add up.
- Date format: keep the format consistent with whatever other dates in the
  document use. If all dates are empty, use ISO YYYY-MM-DD.
- Service lines: if the array has entries already, fill in their empty fields.
  If the array is empty, add 2-4 plausible service lines.
- Never use real personal data. All names, addresses, phones, emails, IDs,
  and identifiers must be invented.
- Do not invent NEW top-level keys or sub-objects. Only fill what the schema
  defines. The response must validate against the provided pydantic schema.
"""


def _load_openai_key() -> str:
    import os
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not found. Add it to parser.env.")
    return key


def fill_db_template(
    template: dict,
    model: str = DEFAULT_MODEL,
    client: Optional[OpenAI] = None,
) -> dict:
    """Take a (possibly sparse) DB JSON dict and return a fully-populated dict."""
    if client is None:
        client = OpenAI(api_key=_load_openai_key())
    user_msg = json.dumps(template, indent=2)
    response = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format=DBEnvelope,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(
            f"Model {model} returned no parsed result. "
            f"Finish reason: {response.choices[0].finish_reason}"
        )
    return parsed.model_dump()


def save_filled_db(data: dict, output_path) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(data, indent=2))
```

- [ ] **Step 3: Write the CLI**

```python
# fill_db.py
"""CLI: fill empty fields in a DB JSON template with the LLM.

Usage:
    python fill_db.py database_schema.json                       # writes <stem>.filled.db.json
    python fill_db.py database_schema.json -o output/case-1.json
"""
import argparse, json, sys
from pathlib import Path

from claims_parser.db_template_filler import fill_db_template, save_filled_db


def main() -> int:
    p = argparse.ArgumentParser(description="Fill empty fields in a canonical DB JSON template.")
    p.add_argument("template", help="Path to a DB JSON file (envelope-wrapped, with possibly empty fields).")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("-m", "--model", default="gpt-5-mini")
    args = p.parse_args()

    tpath = Path(args.template)
    if not tpath.exists():
        print(f"missing: {tpath}", file=sys.stderr); return 1

    if args.output:
        out = Path(args.output)
    else:
        out = tpath.with_suffix("").with_suffix(".filled.db.json") if tpath.suffix == ".json" \
            else tpath.with_name(tpath.name + ".filled.db.json")

    template = json.loads(tpath.read_text())
    filled = fill_db_template(template, model=args.model)
    save_filled_db(filled, out)
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Smoke test**

```bash
.venv-1/bin/python fill_db.py .claude/worktrees/mapping-cache/database_schema.json \
                              -o output/intermediate/case-sample.filled.db.json
```
Expected: every previously empty string is populated with plausible values; fields that already had values are preserved.

- [ ] **Step 5: Commit**

```bash
git add claims_parser/db_template_models.py claims_parser/db_template_filler.py fill_db.py
git commit -m "Add DB-template filler: LLM fills canonical DB JSON template once per case"
```

**End state of Task 0:** Any partial / empty DB JSON template can be expanded into a fully-populated canonical DB JSON. Downstream tasks consume this output indifferent to whether it came from the LLM filler or a production database.

---

## Task 1: Loader + DB schema fingerprint

**Files:**
- Create: `claims_parser/db_loader.py`
- Create: `tests/test_db_loader.py`

- [ ] **Step 1: Write `db_loader.py`**

```python
"""Load the source-of-truth DB JSON, strip the envelope, flatten to dotted paths."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def load_db_json(path: str | Path) -> dict[str, Any]:
    """Read the file and return the inner `result` object (envelope stripped)."""
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or "result" not in raw:
        raise ValueError(f"{path}: expected an object with a top-level 'result' key")
    result = raw["result"]
    if not isinstance(result, dict):
        raise ValueError(f"{path}: 'result' must be an object")
    return result


def flatten_db(data: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts/lists into dotted paths.

    Lists use bracket notation: 'claim.serviceLines[0].procedureCode'.
    Leaves are anything that isn't dict or list (str, int, float, bool, None).
    Empty strings are preserved as-is; the materializer decides how to treat them.
    """
    out: dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else k
            out.update(flatten_db(v, key))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            key = f"{prefix}[{i}]"
            out.update(flatten_db(item, key))
    else:
        out[prefix] = data
    return out


def db_path_get(data: dict, dotted: str) -> Any:
    """Look up a dotted path with [N] list indexing. Returns None if any segment is missing."""
    # parse 'claim.serviceLines[0].procedureCode' into ['claim', 'serviceLines', 0, 'procedureCode']
    parts: list[str | int] = []
    buf = ""
    i = 0
    while i < len(dotted):
        ch = dotted[i]
        if ch == ".":
            if buf:
                parts.append(buf); buf = ""
            i += 1
        elif ch == "[":
            if buf:
                parts.append(buf); buf = ""
            j = dotted.index("]", i)
            parts.append(int(dotted[i+1:j]))
            i = j + 1
        else:
            buf += ch; i += 1
    if buf:
        parts.append(buf)

    cur: Any = data
    for p in parts:
        if cur is None:
            return None
        if isinstance(p, int):
            if not isinstance(cur, list) or p >= len(cur):
                return None
            cur = cur[p]
        else:
            if not isinstance(cur, dict) or p not in cur:
                return None
            cur = cur[p]
    return cur


def db_schema_fingerprint(data: Any) -> str:
    """Hash the set of leaf paths (ignoring list indices and values).

    serviceLines[0].procedureCode and serviceLines[1].procedureCode collapse to
    serviceLines[].procedureCode so cases with different service-line counts
    still share a fingerprint.
    """
    flat = flatten_db(data)
    normed = {_normalize_path(k) for k in flat.keys()}
    body = "\n".join(sorted(normed))
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _normalize_path(path: str) -> str:
    out, i = "", 0
    while i < len(path):
        if path[i] == "[":
            j = path.index("]", i)
            out += "[]"
            i = j + 1
        else:
            out += path[i]; i += 1
    return out
```

- [ ] **Step 2: Write tests**

```python
# tests/test_db_loader.py
from claims_parser.db_loader import (
    load_db_json, flatten_db, db_path_get, db_schema_fingerprint, _normalize_path,
)

SAMPLE = {
    "success": True,
    "result": {
        "patient": {"firstName": "PAV", "lastName": "NUMBO"},
        "claim": {
            "serviceLines": [
                {"procedureCode": "81001"},
                {"procedureCode": "73442"},
            ]
        },
    },
}


def test_load_strips_envelope(tmp_path):
    import json
    p = tmp_path / "db.json"
    p.write_text(json.dumps(SAMPLE))
    out = load_db_json(p)
    assert "patient" in out and "success" not in out


def test_flatten_lists_and_dicts():
    flat = flatten_db(SAMPLE["result"])
    assert flat["patient.firstName"] == "PAV"
    assert flat["claim.serviceLines[0].procedureCode"] == "81001"
    assert flat["claim.serviceLines[1].procedureCode"] == "73442"


def test_db_path_get_handles_missing():
    assert db_path_get(SAMPLE["result"], "patient.firstName") == "PAV"
    assert db_path_get(SAMPLE["result"], "claim.serviceLines[0].procedureCode") == "81001"
    assert db_path_get(SAMPLE["result"], "claim.serviceLines[99].procedureCode") is None
    assert db_path_get(SAMPLE["result"], "patient.nonExistent") is None


def test_fingerprint_ignores_list_cardinality():
    one = {"claim": {"serviceLines": [{"procedureCode": "A"}]}}
    two = {"claim": {"serviceLines": [{"procedureCode": "A"}, {"procedureCode": "B"}]}}
    assert db_schema_fingerprint(one) == db_schema_fingerprint(two)


def test_fingerprint_changes_with_schema():
    a = {"x": 1}
    b = {"x": 1, "y": 2}
    assert db_schema_fingerprint(a) != db_schema_fingerprint(b)


def test_normalize_path_strips_indices():
    assert _normalize_path("claim.serviceLines[0].procedureCode") == "claim.serviceLines[].procedureCode"


if __name__ == "__main__":
    import sys, traceback
    failed = 0
    for name in [n for n in dir() if n.startswith("test_")]:
        try:
            fn = globals()[name]
            import inspect
            if "tmp_path" in inspect.signature(fn).parameters:
                import tempfile, pathlib
                with tempfile.TemporaryDirectory() as d:
                    fn(pathlib.Path(d))
            else:
                fn()
            print(f"  ok  {name}")
        except Exception:
            failed += 1; print(f"  FAIL {name}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 3: Run the tests**

```bash
.venv-1/bin/python -m tests.test_db_loader
```
Expected: 6/6 ok, exit 0.

- [ ] **Step 4: Commit**

```bash
git add claims_parser/db_loader.py tests/test_db_loader.py
git commit -m "Add db_loader: envelope strip, flatten, dotted-path get, schema fingerprint"
```

---

## Task 2: Resolution pydantic models

**Files:**
- Create: `claims_parser/db_mapping_models.py`

- [ ] **Step 1: Write the models exactly as shown in the "Resolution artifact" section above**

(The full code is reproduced there — copy verbatim. Do not paraphrase the field descriptions; they constrain LLM output during the resolution step.)

- [ ] **Step 2: Verify it imports**

```bash
.venv-1/bin/python -c "from claims_parser.db_mapping_models import FieldMap, FieldResolution, Resolution, OptionResolution; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add claims_parser/db_mapping_models.py
git commit -m "Add db_mapping pydantic models: Resolution, FieldResolution, FieldMap"
```

---

## Task 3: Pure coercers (dates, phones, numbers, currency, compose, options)

**Files:**
- Create: `claims_parser/db_coercers.py`
- Create: `tests/test_db_coercers.py`

These functions are pure and stateless. They make the filler trivial in Task 4.

- [ ] **Step 1: Write the coercers**

```python
"""Pure value coercers used by db_filler. No I/O, no LLM, no globals."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional


_DATE_PATTERNS: list[tuple[str, str]] = [
    # (input format used by strptime, regex that matches the raw string)
    ("%Y%m%d",     r"^\d{8}$"),
    ("%m/%d/%Y",   r"^\d{1,2}/\d{1,2}/\d{4}$"),
    ("%m-%d-%Y",   r"^\d{1,2}-\d{1,2}-\d{4}$"),
    ("%Y-%m-%d",   r"^\d{4}-\d{1,2}-\d{1,2}$"),
    ("%d/%m/%Y",   r"^\d{1,2}/\d{1,2}/\d{4}$"),  # ambiguous with US; only used if US fails
]


def coerce_date(raw: str, out_format: Optional[str] = None) -> Optional[str]:
    """Parse `raw` and re-emit in `out_format` (default ISO YYYY-MM-DD).

    Returns None if `raw` doesn't match any known format. Output formats accepted:
    'YYYY-MM-DD', 'MM/DD/YYYY', 'MM-DD-YYYY', 'YYYYMMDD'.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    parsed: Optional[datetime] = None
    for fmt, pat in _DATE_PATTERNS:
        if re.match(pat, s):
            try:
                parsed = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    out_fmt = _DATE_OUT_FORMATS.get(out_format or "YYYY-MM-DD", "%Y-%m-%d")
    return parsed.strftime(out_fmt)


_DATE_OUT_FORMATS = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "MM/DD/YYYY": "%m/%d/%Y",
    "MM-DD-YYYY": "%m-%d-%Y",
    "YYYYMMDD":   "%Y%m%d",
    "DD/MM/YYYY": "%d/%m/%Y",
}


def coerce_phone(raw: str, style: str = "formatted") -> Optional[str]:
    """style='digits' → '9093923823'; style='formatted' → '(909) 392-3823'."""
    if not raw or not isinstance(raw, str):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7:
        return None
    if style == "digits":
        return digits
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return digits  # fall through for non-standard lengths


def coerce_number(raw: str) -> Optional[str]:
    if raw is None or raw == "":
        return None
    s = str(raw).replace(",", "").strip()
    try:
        f = float(s)
    except ValueError:
        return None
    if f == int(f):
        return str(int(f))
    return f"{f}"


def coerce_currency(raw: str) -> Optional[str]:
    if raw is None or raw == "":
        return None
    s = str(raw).replace("$", "").replace(",", "").strip()
    try:
        f = float(s)
    except ValueError:
        return None
    return f"{f:,.2f}"


def apply_transform(raw, transform: Optional[str]) -> Optional[str]:
    """Dispatch on the transform spec string from Resolution.transform."""
    if raw is None or raw == "":
        return None
    if transform is None:
        return str(raw)
    if transform.startswith("date:"):
        return coerce_date(str(raw), out_format=transform.split(":", 1)[1] or None)
    if transform == "phone:digits":
        return coerce_phone(str(raw), style="digits")
    if transform == "phone:formatted" or transform == "phone":
        return coerce_phone(str(raw), style="formatted")
    if transform == "number":
        return coerce_number(str(raw))
    if transform == "currency":
        return coerce_currency(str(raw))
    if transform == "upper":
        return str(raw).upper()
    if transform == "lower":
        return str(raw).lower()
    if transform == "strip":
        return str(raw).strip()
    raise ValueError(f"Unknown transform: {transform!r}")


def compose(values: list, template: str) -> Optional[str]:
    """str.format template, skipping None/empty values cleanly.

    Empty parts produce a clean single-space join; a fully-empty result returns None.
    """
    stringified = ["" if v is None else str(v) for v in values]
    rendered = template.format(*stringified)
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return rendered if rendered else None
```

- [ ] **Step 2: Write the tests**

```python
# tests/test_db_coercers.py
from claims_parser.db_coercers import (
    coerce_date, coerce_phone, coerce_number, coerce_currency,
    apply_transform, compose,
)


def test_date_yyyymmdd_to_iso():
    assert coerce_date("20250606") == "2025-06-06"

def test_date_yyyymmdd_to_us():
    assert coerce_date("20250606", out_format="MM/DD/YYYY") == "06/06/2025"

def test_date_us_to_yyyymmdd():
    assert coerce_date("01/23/2024", out_format="YYYYMMDD") == "20240123"

def test_date_garbage_returns_none():
    assert coerce_date("not a date") is None

def test_phone_formatted_us():
    assert coerce_phone("9093923823") == "(909) 392-3823"

def test_phone_formatted_with_extension_chars():
    assert coerce_phone("909-392-8233") == "(909) 392-8233"

def test_phone_digits():
    assert coerce_phone("(909) 392-8233", style="digits") == "9093928233"

def test_number_integer():
    assert coerce_number("4") == "4"

def test_number_with_commas():
    assert coerce_number("5,732.32") == "5732.32"

def test_currency():
    assert coerce_currency("5732.32") == "5,732.32"

def test_apply_transform_date():
    assert apply_transform("20250606", "date:MM/DD/YYYY") == "06/06/2025"

def test_apply_transform_passthrough():
    assert apply_transform("plain", None) == "plain"

def test_apply_transform_unknown_raises():
    try:
        apply_transform("x", "weird:thing")
    except ValueError:
        return
    raise AssertionError("expected ValueError")

def test_compose_name():
    assert compose(["PAV", "LOO", "NUMBO"], "{0} {1} {2}") == "PAV LOO NUMBO"

def test_compose_with_empty_middle():
    assert compose(["PAV", "", "NUMBO"], "{0} {1} {2}") == "PAV NUMBO"

def test_compose_all_empty_returns_none():
    assert compose(["", "", ""], "{0} {1} {2}") is None


if __name__ == "__main__":
    import sys, traceback
    failed = 0
    for name in [n for n in dir() if n.startswith("test_")]:
        try:
            globals()[name]()
            print(f"  ok  {name}")
        except Exception:
            failed += 1; print(f"  FAIL {name}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 3: Run the tests**

```bash
.venv-1/bin/python -m tests.test_db_coercers
```
Expected: 16/16 ok.

- [ ] **Step 4: Commit**

```bash
git add claims_parser/db_coercers.py tests/test_db_coercers.py
git commit -m "Add db_coercers: dates, phones, numbers, currency, compose, transforms"
```

---

## Task 4: The deterministic filler

This is the load-bearing module. It must be testable with a hand-written `FieldMap` against the real DB JSON so we can prove materialization correctness before the mapper exists.

**Files:**
- Create: `claims_parser/db_filler.py`
- Create: `tests/test_db_filler.py`

- [ ] **Step 1: Write `db_filler.py`**

```python
"""Apply a FieldMap to a DB JSON to produce a FilledFormSchema.

Pure, deterministic, no network. Every behavior is driven by the FieldMap;
no form-specific knowledge lives in this module.
"""
from __future__ import annotations

from typing import Any, Optional

from claims_parser.db_coercers import apply_transform, compose
from claims_parser.db_loader import db_path_get
from claims_parser.db_mapping_models import (
    FieldMap, FieldResolution, OptionResolution, Resolution,
)
from claims_parser.filler_models import FilledFormField, FilledFormSchema
from claims_parser.schema_models import FormField, FormSchema


def fill_from_db(schema: FormSchema, fmap: FieldMap, db: dict) -> FilledFormSchema:
    resolutions_by_id = {fr.field_id: fr for fr in fmap.field_resolutions}
    filled_fields: list[FilledFormField] = []
    for f in schema.fields:
        fr = resolutions_by_id.get(f.field_id)
        filled_fields.append(_fill_field(f, fr, db))
    return FilledFormSchema.from_schema(schema, filled_fields)


def _fill_field(field: FormField, fr: Optional[FieldResolution], db: dict) -> FilledFormField:
    if fr is None:
        return _empty(field, reason="no resolution in fillmap")
    if field.field_type in ("checkbox", "radio_group"):
        return _fill_choice(field, fr, db)
    raw = _resolve_value(fr.value, db)
    val = apply_transform(raw, fr.value.transform) if raw is not None else None
    return FilledFormField(**field.model_dump(), value=val)


def _fill_choice(field: FormField, fr: FieldResolution, db: dict) -> FilledFormField:
    selected: list[str] = []
    for opt in fr.options:
        if _option_matches(opt, db):
            selected.append(opt.option_label)
    if field.field_type == "radio_group":
        value = selected[0] if selected else None
    else:  # checkbox
        value = selected if selected else None
    return FilledFormField(**field.model_dump(), value=value)


def _option_matches(opt: OptionResolution, db: dict) -> bool:
    val = _resolve_value(opt.when, db)
    if opt.selected_if == "always":
        return val is not None and val != ""
    if opt.selected_if == "equals":
        return str(val) == (opt.equals_value or "")
    # truthy
    if val is None or val == "" or val is False:
        return False
    return True


def _resolve_value(r: Resolution, db: dict) -> Any:
    if r.kind == "constant":
        return r.literal
    if r.kind == "unmapped":
        return None
    if r.kind == "scalar":
        return db_path_get(db, r.paths[0]) if r.paths else None
    if r.kind == "compose":
        raws = [db_path_get(db, p) for p in r.paths]
        composed = compose(raws, r.template or "")
        return composed
    if r.kind == "service_line":
        # paths[0] is the per-row field path with placeholder, e.g.
        # 'claim.serviceLines[{i}].procedureCode'
        idx = r.service_line_index or 0
        if not r.paths:
            return None
        path = r.paths[0].replace("{i}", str(idx))
        return db_path_get(db, path)
    if r.kind == "derived":
        d = r.derive or {}
        path = d.get("path", "")
        observed = db_path_get(db, path)
        op = d.get("op", "eq")
        if op == "eq":
            matched = str(observed) == str(d.get("value", ""))
        elif op == "neq":
            matched = str(observed) != str(d.get("value", ""))
        elif op == "non_empty":
            matched = observed not in (None, "")
        elif op == "empty":
            matched = observed in (None, "")
        else:
            raise ValueError(f"Unknown derived op: {op!r}")
        return d.get("true_value") if matched else d.get("false_value")
    raise ValueError(f"Unknown Resolution.kind: {r.kind!r}")


def _empty(field: FormField, reason: str = "") -> FilledFormField:
    return FilledFormField(**field.model_dump(), value=None)
```

- [ ] **Step 2: Write the TDD harness**

Test against a small synthetic schema and a small synthetic DB. Then add one integration test that loads the real `database_schema.json` plus a tiny three-field hand-authored fillmap to prove the full flow works on real data.

```python
# tests/test_db_filler.py
from claims_parser.db_filler import fill_from_db
from claims_parser.db_mapping_models import (
    FieldMap, FieldResolution, OptionResolution, Resolution,
)
from claims_parser.schema_models import FormField, FormSchema


DB = {
    "patient": {"firstName": "PAV", "middleName": "LOO", "lastName": "NUMBO",
                "dateOfBirth": "19761101", "relationshipCode": "18"},
    "claim": {
        "claimNumber": "3235234235235",
        "serviceLines": [
            {"procedureCode": "81001", "charges": "86.23", "serviceDateStart": "01/23/2026"},
            {"procedureCode": "73442", "charges": "86.23", "serviceDateStart": "03/23/2026"},
        ],
    },
}


def _f(field_id, **kw):
    kw.setdefault("label", field_id.replace("_", " ").title())
    kw.setdefault("section", None)
    kw.setdefault("field_type", "text")
    kw.setdefault("required", False)
    kw.setdefault("source_text", kw["label"])
    return FormField(field_id=field_id, **kw)


def _fr(field_id, value: Resolution, options=None, confidence="high"):
    return FieldResolution(
        field_id=field_id, confidence=confidence, value=value, options=options or [],
    )


def _empty_fmap(resolutions):
    return FieldMap(
        schema_fingerprint="x", db_schema_fingerprint="y",
        form_pdf_basename="t.pdf", created_at="2026-06-02T00:00:00Z",
        field_resolutions=resolutions,
    )


def test_scalar_passthrough():
    schema = FormSchema(sections=[], fields=[_f("first_name")])
    fmap = _empty_fmap([
        _fr("first_name", Resolution(kind="scalar", paths=["patient.firstName"], reason="t")),
    ])
    out = fill_from_db(schema, fmap, DB)
    assert out.fields[0].value == "PAV"


def test_compose_name_skips_empty_middle():
    db = {"patient": {"firstName": "A", "middleName": "", "lastName": "C"}}
    schema = FormSchema(sections=[], fields=[_f("patient_name")])
    fmap = _empty_fmap([
        _fr("patient_name", Resolution(
            kind="compose",
            paths=["patient.firstName", "patient.middleName", "patient.lastName"],
            template="{0} {1} {2}",
            reason="t")),
    ])
    out = fill_from_db(schema, fmap, db)
    assert out.fields[0].value == "A C"


def test_date_transform():
    schema = FormSchema(sections=[], fields=[_f("dob", field_type="date", format_hint="MM/DD/YYYY")])
    fmap = _empty_fmap([
        _fr("dob", Resolution(
            kind="scalar", paths=["patient.dateOfBirth"], transform="date:MM/DD/YYYY", reason="t")),
    ])
    out = fill_from_db(schema, fmap, DB)
    assert out.fields[0].value == "11/01/1976"


def test_service_line_indexed():
    schema = FormSchema(sections=[], fields=[
        _f("proc_row_1"), _f("proc_row_2"),
    ])
    fmap = _empty_fmap([
        _fr("proc_row_1", Resolution(
            kind="service_line", paths=["claim.serviceLines[{i}].procedureCode"],
            service_line_index=0, reason="t")),
        _fr("proc_row_2", Resolution(
            kind="service_line", paths=["claim.serviceLines[{i}].procedureCode"],
            service_line_index=1, reason="t")),
    ])
    out = fill_from_db(schema, fmap, DB)
    assert out.fields[0].value == "81001"
    assert out.fields[1].value == "73442"


def test_service_line_out_of_bounds_is_none():
    schema = FormSchema(sections=[], fields=[_f("proc_row_99")])
    fmap = _empty_fmap([
        _fr("proc_row_99", Resolution(
            kind="service_line", paths=["claim.serviceLines[{i}].procedureCode"],
            service_line_index=99, reason="t")),
    ])
    out = fill_from_db(schema, fmap, DB)
    assert out.fields[0].value is None


def test_derived_yes_no_from_relationship():
    schema = FormSchema(sections=[], fields=[
        _f("patient_is_subscriber", field_type="radio_group", options=["Yes", "No"]),
    ])
    fmap = _empty_fmap([
        _fr("patient_is_subscriber",
            Resolution(kind="constant", literal=None, reason="choice-driven"),
            options=[
                OptionResolution(option_label="Yes",
                    when=Resolution(kind="derived", derive={
                        "op": "eq", "path": "patient.relationshipCode",
                        "value": "18", "true_value": "yes", "false_value": "",
                    }, reason="self"),
                    selected_if="equals", equals_value="yes"),
                OptionResolution(option_label="No",
                    when=Resolution(kind="derived", derive={
                        "op": "neq", "path": "patient.relationshipCode",
                        "value": "18", "true_value": "yes", "false_value": "",
                    }, reason="not-self"),
                    selected_if="equals", equals_value="yes"),
            ]),
    ])
    out = fill_from_db(schema, fmap, DB)
    assert out.fields[0].value == "Yes"


def test_no_resolution_leaves_value_none():
    schema = FormSchema(sections=[], fields=[_f("totally_unmapped")])
    out = fill_from_db(schema, _empty_fmap([]), DB)
    assert out.fields[0].value is None


def test_real_db_three_fields():
    from claims_parser.db_loader import load_db_json
    import pathlib
    p = pathlib.Path(".claude/worktrees/mapping-cache/database_schema.json")
    if not p.exists():
        return  # skip in environments without the sample
    db = load_db_json(p)
    schema = FormSchema(sections=[], fields=[
        _f("member_id"), _f("claim_number"),
        _f("patient_full_name", label="Patient Name"),
    ])
    fmap = _empty_fmap([
        _fr("member_id", Resolution(kind="scalar", paths=["patient.memberID"], reason="t")),
        _fr("claim_number", Resolution(kind="scalar", paths=["claim.claimNumber"], reason="t")),
        _fr("patient_full_name", Resolution(
            kind="compose",
            paths=["patient.firstName", "patient.middleName", "patient.lastName"],
            template="{0} {1} {2}", reason="t")),
    ])
    out = fill_from_db(schema, fmap, db)
    assert out.fields[0].value == "YTS0238343432"
    assert out.fields[1].value == "3235234235235"
    assert out.fields[2].value == "PAVITRA LOO NUMBO"


if __name__ == "__main__":
    import sys, traceback
    failed = 0
    for name in [n for n in dir() if n.startswith("test_")]:
        try:
            globals()[name]()
            print(f"  ok  {name}")
        except Exception:
            failed += 1; print(f"  FAIL {name}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 3: Run the tests**

```bash
.venv-1/bin/python -m tests.test_db_filler
```
Expected: 8/8 ok.

- [ ] **Step 4: Commit**

```bash
git add claims_parser/db_filler.py tests/test_db_filler.py
git commit -m "Add db_filler: deterministic Resolution -> FilledFormSchema materialization"
```

**End state of Task 4:** The materialization side is fully functional. We can build a `FieldMap` by hand, point it at the real DB JSON, and produce a correct `filled.json`. Every later task is a layer on top of this proven foundation.

---

## Task 5: Heuristic resolver (the fast-path before LLM)

For each `FormField`, attempt a no-LLM match. If the match is high-confidence, skip the LLM call for that field.

**Files:**
- Create: `claims_parser/db_mapper.py` (partial — heuristic side only)
- Create: `tests/test_db_mapper_heuristic.py`

Strategy:

1. **Token-set Jaccard** between the field's tokens (from `field_id`, `label`, `section`) and each DB path's tokens.
2. **Block-name boost**: if the field's `section` contains "Patient", paths under `patient.*` get a similarity bonus.
3. **Type-aware filtering**: `field_type=date` only matches DB paths that look like dates by name (`date`, `dob`, `dateOfService`, etc.).
4. **Threshold**: similarity ≥ 0.7 → high confidence, emit `Resolution(kind="scalar", ...)`. Otherwise mark unresolved (LLM will handle).

- [ ] **Step 1: Write the heuristic resolver**

```python
"""Heuristic (no-LLM) form-field → DB-path resolution. Fast-path only.

Confidence thresholds are intentionally conservative; ambiguous matches
must reach the LLM step instead of being wrong-but-cheap.
"""
from __future__ import annotations

import re
from typing import Optional

from claims_parser.db_mapping_models import FieldResolution, Resolution
from claims_parser.schema_models import FormField


_DATE_PATH_HINTS = ("date", "dob", "dateofbirth", "dateofservice", "effectivedate", "datefrom", "dateto")
_PHONE_PATH_HINTS = ("phone", "fax")
_EMAIL_PATH_HINTS = ("email",)
_BLOCK_HINTS = {
    "patient": ("patient",),
    "subscriber": ("subscriber", "insured", "policyholder"),
    "dependent": ("dependent",),
    "provider": ("provider", "facility", "billing", "rendering", "service"),
    "payer": ("payer", "insurer", "insurance", "plan"),
    "claim": ("claim", "appeal", "denial"),
}


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _block_for_section(section: Optional[str]) -> Optional[str]:
    if not section:
        return None
    sl = section.lower()
    for block, hints in _BLOCK_HINTS.items():
        if any(h in sl for h in hints):
            return block
    return None


def heuristic_resolve(
    field: FormField, normalized_db_paths: list[str], confidence_floor: float = 0.7,
) -> Optional[FieldResolution]:
    """Return a high-confidence FieldResolution or None (LLM should decide)."""
    if field.field_type in ("checkbox", "radio_group"):
        return None  # always punt to LLM — option mapping is too nuanced

    f_toks = _tokens(field.field_id) | _tokens(field.label)
    if field.section:
        f_toks |= _tokens(field.section)

    preferred_block = _block_for_section(field.section)

    best: tuple[float, str] = (0.0, "")
    for path in normalized_db_paths:
        p_toks = _tokens(path)
        if not p_toks:
            continue
        # Type filter
        if field.field_type == "date" and not any(h in path.lower() for h in _DATE_PATH_HINTS):
            continue
        if field.field_type == "phone" and not any(h in path.lower() for h in _PHONE_PATH_HINTS):
            continue
        if field.field_type == "email" and not any(h in path.lower() for h in _EMAIL_PATH_HINTS):
            continue
        jaccard = len(f_toks & p_toks) / max(1, len(f_toks | p_toks))
        if preferred_block and path.startswith(preferred_block + "."):
            jaccard += 0.15  # soft bias
        if jaccard > best[0]:
            best = (jaccard, path)

    score, path = best
    if score < confidence_floor or not path:
        return None
    return FieldResolution(
        field_id=field.field_id,
        confidence="high",
        value=Resolution(
            kind="scalar",
            paths=[path],
            transform=_default_transform_for_type(field, path),
            reason=f"heuristic jaccard={score:.2f}",
        ),
    )


def _default_transform_for_type(field: FormField, path: str) -> Optional[str]:
    if field.field_type == "date":
        return f"date:{field.format_hint or 'YYYY-MM-DD'}"
    if field.field_type == "phone":
        return "phone:formatted"
    if field.field_type == "currency":
        return "currency"
    if field.field_type == "number":
        return "number"
    return None
```

- [ ] **Step 2: Tests against the real DB schema**

```python
# tests/test_db_mapper_heuristic.py
from claims_parser.db_loader import flatten_db, load_db_json, _normalize_path
from claims_parser.db_mapper import heuristic_resolve
from claims_parser.schema_models import FormField

import pathlib

DB_PATH = pathlib.Path(".claude/worktrees/mapping-cache/database_schema.json")


def _paths():
    if not DB_PATH.exists():
        return []
    db = load_db_json(DB_PATH)
    return sorted({_normalize_path(k) for k in flatten_db(db).keys()})


def _f(field_id, label, **kw):
    kw.setdefault("section", None)
    kw.setdefault("field_type", "text")
    kw.setdefault("required", False)
    kw.setdefault("source_text", label)
    return FormField(field_id=field_id, label=label, **kw)


def test_member_id_matches_patient_block_when_section_says_patient():
    paths = _paths()
    if not paths: return
    f = _f("member_id", "Member ID", section="Patient Information")
    r = heuristic_resolve(f, paths)
    assert r is not None and r.value.paths[0] == "patient.memberID"


def test_member_id_with_subscriber_section_picks_subscriber():
    paths = _paths()
    if not paths: return
    f = _f("subscriber_member_id", "Subscriber Member ID", section="Subscriber")
    r = heuristic_resolve(f, paths)
    assert r is not None and r.value.paths[0] == "subscriber.memberID"


def test_claim_number_finds_claim_number():
    paths = _paths()
    if not paths: return
    f = _f("claim_number", "Claim Number", section="Claim")
    r = heuristic_resolve(f, paths)
    assert r is not None and r.value.paths[0] == "claim.claimNumber"


def test_radio_group_punts_to_llm():
    paths = _paths()
    if not paths: return
    f = _f("appeal_method", "Appeal Method", field_type="radio_group")
    assert heuristic_resolve(f, paths) is None


def test_no_signal_returns_none():
    paths = _paths()
    if not paths: return
    f = _f("zorblax_qwerty", "Zorblax Qwerty")
    assert heuristic_resolve(f, paths) is None


if __name__ == "__main__":
    import sys, traceback
    failed = 0
    for name in [n for n in dir() if n.startswith("test_")]:
        try:
            globals()[name]()
            print(f"  ok  {name}")
        except Exception:
            failed += 1; print(f"  FAIL {name}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 3: Run the tests**

```bash
.venv-1/bin/python -m tests.test_db_mapper_heuristic
```
Expected: 5/5 ok (gracefully skip if `database_schema.json` is absent).

- [ ] **Step 4: Commit**

```bash
git add claims_parser/db_mapper.py tests/test_db_mapper_heuristic.py
git commit -m "Add heuristic db-mapper fast-path with token Jaccard + section/type bias"
```

---

## Task 6: LLM resolver (residuals + choice fields + service-line tables)

Only invoked for fields the heuristic couldn't resolve. One LLM call per form, batched.

**Files:**
- Modify: `claims_parser/db_mapper.py` (add `llm_resolve` + `resolve_form`)

- [ ] **Step 1: Add the LLM resolver to `db_mapper.py`**

Use `client.chat.completions.parse(response_format=LLMResolverResponse)` with `gpt-5-mini`. The response model is a pydantic wrapper holding `list[FieldResolution]`. System prompt enumerates exactly the available DB paths so the model cannot hallucinate paths.

System prompt body (verbatim — domain-generic):

```
You receive a list of form fields and the FLATTENED list of available DB paths
(dotted notation, e.g. "patient.firstName" or "claim.serviceLines[].procedureCode").

For each form field, decide which DB path supplies its value. Produce a
FieldResolution with:
- kind="scalar" for single-path mappings
- kind="compose" with paths + template (e.g. "{0} {1} {2}") when one form field
  combines multiple DB fields (e.g. "Patient Name" from firstName/middleName/lastName)
- kind="service_line" with paths using "[{i}]" placeholder and service_line_index
  for cells belonging to a service-line table row
- kind="derived" for fields like "Patient is Subscriber? Yes/No" derivable from
  a predicate over the DB (eq, neq, non_empty, empty)
- kind="unmapped" when no DB path plausibly supplies the value

For radio_group / checkbox fields, produce one OptionResolution per option,
matching the option's label to the appropriate DB path or derived condition.

Rules:
- NEVER invent paths not in the provided list.
- NEVER reference field labels, section names, or option values from any
  specific form. Work only from what you are given.
- Apply a transform when the field_type clearly requires it
  (date:<format>, phone:formatted, number, currency).
- Use the form field's format_hint as the date output format when present.
- For service-line tables: assign service_line_index 0, 1, 2, ... based on
  the field's row position inferred from field_id or label.
- Confidence: "high" for unambiguous matches, "medium" for plausible,
  "low" for guesses, "unmapped" when no plausible path exists.
```

Function signature:

```python
def resolve_form(
    schema: FormSchema, db: dict, model: str = "gpt-5-mini",
    client: Optional[OpenAI] = None,
) -> FieldMap:
    """Full resolution: heuristic for each field, LLM batch for the rest."""
    ...
```

Internally:
1. Compute `normalized_db_paths` from `flatten_db(db)`.
2. For each field, try `heuristic_resolve`. Collect successes.
3. Send the residuals + the path list to the LLM with structured output.
4. Merge into a single `FieldMap`.

- [ ] **Step 2: Smoke test (manual, not committed)**

```bash
.venv-1/bin/python -c "
from claims_parser.db_loader import load_db_json
from claims_parser.db_mapper import resolve_form
from claims_parser.schema_models import load_schema
db = load_db_json('.claude/worktrees/mapping-cache/database_schema.json')
schema = load_schema('output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.schema.json')
fmap = resolve_form(schema, db)
print(f'resolved={sum(1 for r in fmap.field_resolutions if r.confidence != \"unmapped\")}/'
      f'{len(fmap.field_resolutions)}')
"
```

- [ ] **Step 3: Commit**

```bash
git add claims_parser/db_mapper.py
git commit -m "Add LLM resolver: residual fields + options + service-line tables"
```

---

## Task 7: FieldMap cache

Don't re-run the LLM if the form hasn't changed. Mirror the existing `mapping_cache.py` pattern.

**Files:**
- Modify: `claims_parser/db_mapper.py`

- [ ] **Step 1: Add `save_fillmap` / `load_fillmap` / `compute_schema_fingerprint`**

```python
def compute_schema_fingerprint(schema: FormSchema) -> str:
    """Stable hash over (field_id, label, section, field_type, sorted options) tuples.
    Excludes source_text — that's spatial info, irrelevant to data mapping."""
    import hashlib, json
    body = json.dumps(
        [
            {
                "field_id": f.field_id, "label": f.label, "section": f.section,
                "field_type": f.field_type,
                "options": sorted(f.options) if f.options else None,
            }
            for f in schema.fields
        ],
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def save_fillmap(fmap: FieldMap, output_path) -> None: ...
def load_fillmap(input_path) -> FieldMap: ...
```

- [ ] **Step 2: Wire cache into `resolve_form`**

```python
def resolve_form(schema, db, fillmap_path=None, model=..., client=None) -> FieldMap:
    schema_fp = compute_schema_fingerprint(schema)
    db_fp = db_schema_fingerprint(db)
    if fillmap_path and Path(fillmap_path).exists():
        cached = load_fillmap(fillmap_path)
        if cached.schema_fingerprint == schema_fp and cached.db_schema_fingerprint == db_fp:
            return cached
    # ... heuristic + LLM ...
    fmap.schema_fingerprint = schema_fp
    fmap.db_schema_fingerprint = db_fp
    if fillmap_path:
        save_fillmap(fmap, fillmap_path)
    return fmap
```

- [ ] **Step 3: Commit**

```bash
git add claims_parser/db_mapper.py
git commit -m "Cache FieldMap to <stem>.fillmap.json keyed by schema+DB fingerprints"
```

---

## Task 8: `map_db.py` root CLI

**Files:**
- Create: `map_db.py`

- [ ] **Step 1: Write the CLI**

```python
"""CLI: schema + DB JSON -> per-form FieldMap (resolution cache)."""
import argparse, sys
from pathlib import Path

from claims_parser import load_schema
from claims_parser.db_loader import load_db_json
from claims_parser.db_mapper import resolve_form, save_fillmap

INTERMEDIATE_DIR = Path("output/intermediate")


def main() -> int:
    p = argparse.ArgumentParser(description="Resolve a FormSchema against a DB JSON. Writes <stem>.fillmap.json.")
    p.add_argument("schema")
    p.add_argument("db_json")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("-m", "--model", default="gpt-5-mini")
    p.add_argument("--refresh", action="store_true", help="Ignore the cache and re-resolve.")
    args = p.parse_args()
    # ... orchestrate, save to default path: output/intermediate/<stem>.fillmap.json
```

- [ ] **Step 2: Commit**

```bash
git add map_db.py
git commit -m "Add map_db.py CLI: produce <stem>.fillmap.json"
```

---

## Task 9: `fill_schema.py --db-json` integration

Bring the new path into the existing entry point so all three branches consume it transparently.

**Files:**
- Modify: `fill_schema.py`

Behavior:
- Without `--db-json`: existing LLM filler (no change).
- With `--db-json PATH`: load DB, load schema, ensure fillmap exists (auto-run `resolve_form` if missing), then `fill_from_db` → save `filled.json`.

- [ ] **Step 1: Add the flag and branch logic**

```python
p.add_argument("--db-json", default=None, help="Path to DB source-of-truth JSON. If set, skip the LLM filler.")
p.add_argument("--fillmap", default=None, help="Pre-computed fillmap. If absent, derive from schema stem.")
# ...
if args.db_json:
    db = load_db_json(args.db_json)
    fillmap_path = Path(args.fillmap) if args.fillmap else INTERMEDIATE_DIR / f"{stem}.fillmap.json"
    fmap = resolve_form(schema, db, fillmap_path=fillmap_path, model=args.model)
    filled = fill_from_db(schema, fmap, db)
else:
    filled = fill_form_schema(schema, model=args.model)
```

- [ ] **Step 2: Commit**

```bash
git add fill_schema.py
git commit -m "Wire --db-json into fill_schema.py; LLM filler stays as fallback"
```

---

## Task 10: Review-report `unmapped_db_key` reason

**Files:**
- Modify: `claims_parser/review_models.py`
- Modify: `claims_parser/db_filler.py` (record per-field reason)
- Modify: the three writers (consume the reason if value is None)

- [ ] **Step 1: Add the reason to the Literal**

In `review_models.py`, extend `ReviewReason` (or whatever the field is named) to include `"unmapped_db_key"`.

- [ ] **Step 2: Surface it from the filler**

`db_filler.fill_from_db` should optionally return a sidecar `dict[field_id, reason]` so the writers can tag review items appropriately. Simpler alternative: leave value `None` and let writers tag as `missing_value` — acceptable v1; defer this task if scope tightens.

- [ ] **Step 3: Commit**

```bash
git add claims_parser/review_models.py claims_parser/db_filler.py \
        claims_parser/pdf_writer.py claims_parser/acroform_writer.py claims_parser/mapped_writer.py
git commit -m "Add unmapped_db_key review reason for fields with no DB source"
```

---

## Task 11: Repo hygiene — gitignore + `__init__.py` exports

**Files:**
- Modify: `.gitignore`
- Modify: `claims_parser/__init__.py`

- [ ] **Step 1: Add `database/` to `.gitignore`**

The DB JSON may contain PHI. Treat it like `input/`.

```
# DB source-of-truth JSONs (may contain PHI)
database/
```

- [ ] **Step 2: Export public surface in `claims_parser/__init__.py`**

```python
from claims_parser.db_loader import load_db_json, flatten_db
from claims_parser.db_mapper import resolve_form, save_fillmap, load_fillmap
from claims_parser.db_filler import fill_from_db
from claims_parser.db_mapping_models import FieldMap, FieldResolution, Resolution
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore claims_parser/__init__.py
git commit -m "Hide database/ from git; export new db_* surface"
```

---

## Task 12: End-to-end smoke on all three branches

Run the full pipeline against a real PDF + the sample DB JSON for each branch. Compare review reports.

- [ ] **Non-editable branch**

```bash
.venv-1/bin/python extract.py      input/AETNA_Form1.pdf
.venv-1/bin/python build_schema.py output/intermediate/AETNA_Form1.context.json
.venv-1/bin/python map_db.py       output/intermediate/AETNA_Form1.schema.json database_schema.json
.venv-1/bin/python fill_schema.py  output/intermediate/AETNA_Form1.schema.json --db-json database_schema.json
.venv-1/bin/python write_pdf.py    input/AETNA_Form1.pdf output/intermediate/AETNA_Form1.filled.json
```

- [ ] **AcroForm (direct) branch**

```bash
.venv-1/bin/python acroform_extract.py input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf
.venv-1/bin/python map_db.py           output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.schema.json database_schema.json
.venv-1/bin/python fill_schema.py      output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.schema.json --db-json database_schema.json
.venv-1/bin/python acroform_write.py   input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.filled.json
```

- [ ] **Mapped AcroForm branch**

```bash
.venv-1/bin/python map_widgets_cached.py input/X.pdf
.venv-1/bin/python map_db.py             output/intermediate/X.schema.json database_schema.json
.venv-1/bin/python fill_schema.py        output/intermediate/X.schema.json --db-json database_schema.json
.venv-1/bin/python mapped_write.py       input/X.pdf output/intermediate/X.filled.json output/intermediate/X.binding.json
```

- [ ] **Compare reviews vs the old LLM filler**

Run the same forms with and without `--db-json`; diff the review reports. Expectations:
- LLM filler: zero `missing_value` (it filled everything fictitiously).
- DB filler: `missing_value` count == number of form fields with no DB source. This is the intended behavior — the manual-review list is the deliverable for fields the DB doesn't cover.

- [ ] **No commit needed unless a fix surfaces.**

---

## Open questions for the user (answer before Task 6 — they affect prompts)

1. **`patient` vs `subscriber` precedence.** When `patient.relationshipCode == "18"` (Self), both blocks carry the same data. Should a form's "Subscriber" block draw from `subscriber.*` (literal) or fall through to `patient.*` if `subscriber.*` is empty? Default proposal: literal `subscriber.*`; the DB writer is responsible for populating both blocks.
2. **Service-line cardinality on the form.** Forms have a fixed number of service-line rows; DB has a variable count. If DB has more rows than the form supports, surface the overflow in the review report? Default: yes, new review reason `service_lines_overflow`.
3. **Form-fill date.** Use `claim.appealsFilingDate` as the "Date" for the form, or stamp today's date? Default: `claim.appealsFilingDate`.
4. ~~Should the LLM filler stay in the codebase?~~ **Resolved (2026-06-02):** the existing per-form `form_filler.py` is superseded by the new DB-template filler (Task 0). Same model (`gpt-5-mini`), same library, but applied to a fixed canonical schema instead of per-form. Keep `form_filler.py` in tree only for cases where a form has fields with no canonical-DB analog and you still want dummy values — not the default path.
5. **Hybrid fallback** (LLM filler for fields the DB doesn't cover)? Default proposal: **no**. The whole point is determinism — flag the gaps in review and let a human resolve them. The DB-template filler in Task 0 already covers all fields *defined in the canonical schema*; the question is only about form fields with no canonical analog.
6. **Where do DB JSONs live in the repo?** Proposed: `database/` (gitignored). Confirm before Task 11.

---

## Files **not** modified (the contract that protects them)

- `claims_parser/extractor.py`, `claims_parser/schema_builder.py` — Agent 1 unchanged.
- `claims_parser/acroform_extractor.py`, `acroform_label_inference.py`, `widget_mapper.py` — extraction/mapping unchanged.
- `claims_parser/pdf_writer.py`, `acroform_writer.py`, `mapped_writer.py` — writers consume `<stem>.filled.json` as today (only review-reason addition in Task 10).
- `claims_parser/anchor_placer.py`, vision-pass logic — untouched.
- `claims_parser/form_filler.py` — kept as fallback. Not deprecated.

The integration boundary is `output/intermediate/<stem>.filled.json`. The contract: a `FilledFormSchema` whose values respect `field_type` rules. If we preserve that, the writers never need to know whether the values came from an LLM or a database.

---

## Suggested execution order

If the implementing agent is constrained on time, the highest-value tasks in order are:

1. **Task 0** — DB-template filler. Single LLM call against the canonical schema produces a realistic dummy DB JSON. Unblocks everything else without needing real data.
2. **Tasks 1 + 2 + 3 + 4** — full materialization with hand-authored FieldMaps. This already lets the user manually map one form and produce a correct PDF end-to-end. **Stop here for a working demo.**
3. **Task 5** — heuristic resolver cuts most per-form LLM cost.
4. **Tasks 6 + 7** — full LLM resolution with cache. Production-grade.
5. **Tasks 8 + 9 + 11** — CLI ergonomics.
6. **Task 10** — review-report polish.
7. **Task 12** — full E2E across all three branches.

## End-to-end pipeline (after all tasks complete)

```
database_schema.json (template, partial or empty)
   ↓  [Task 0]   fill_db.py  ←  one LLM call against fixed canonical schema
filled-db.json (fully populated)
   ↓  [Task 6+7] map_db.py    ←  one LLM call per FORM (cached as <stem>.fillmap.json)
<stem>.fillmap.json (form-keyed, reusable across cases)
   ↓  [Task 4]  fill_schema.py --db-json filled-db.json
<stem>.filled.json (the existing contract)
   ↓  existing writers, untouched
final/<stem>.filled.pdf
```

Two distinct LLM surfaces:
- **DB-template filler** (Task 0) — once per case, fixed prompt, fixed pydantic shape.
- **Form mapper** (Task 6) — once per form, cached forever, fixed pydantic shape (`FieldMap`).

Everything between is deterministic Python.
