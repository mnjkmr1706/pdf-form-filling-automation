"""CLI: fill a FormSchema with values.

Two modes:
  - Default (LLM filler / Agent 2): generates plausible dummy data per form.
  - --db-json PATH: deterministic fill from the canonical DB JSON via the
    resolve+materialize pipeline. Auto-runs the resolver on first use; the
    resulting <stem>.fillmap.json is cached for subsequent fills.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from claims_parser import fill_form_schema, load_schema, save_filled_schema

INTERMEDIATE_DIR = Path("output/intermediate")


def _strip_schema_suffix(name: str) -> str:
    for suf in (".schema.json", ".json"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def main() -> int:
    p = argparse.ArgumentParser(description="Fill a form schema with values (LLM or DB-driven).")
    p.add_argument("schema", help="Path to the FormSchema JSON produced by build_schema.py or acroform_extract.py.")
    p.add_argument("-o", "--output", default=None,
                   help="Where to save the filled schema (default: output/intermediate/<stem>.filled.json).")
    p.add_argument("-m", "--model", default="gpt-5-mini")
    p.add_argument("--db-json", default=None,
                   help="Canonical DB JSON file. If set, deterministic DB-driven fill is used instead of the LLM.")
    p.add_argument("--fillmap", default=None,
                   help="Pre-computed fillmap path. Default: output/intermediate/<stem>.fillmap.json.")
    p.add_argument("--refresh-fillmap", action="store_true",
                   help="Ignore an existing fillmap cache and re-resolve.")
    args = p.parse_args()

    schema_path = Path(args.schema)
    if not schema_path.exists():
        print(f"missing: {schema_path}", file=sys.stderr)
        return 1

    stem = _strip_schema_suffix(schema_path.name)
    output_path = Path(args.output) if args.output else INTERMEDIATE_DIR / f"{stem}.filled.json"

    print(f"-> Loading schema: {schema_path}")
    schema = load_schema(schema_path)
    print(f"   {len(schema.fields)} fields, {len(schema.sections)} sections")

    if args.db_json:
        from claims_parser.db_filler import fill_from_db
        from claims_parser.db_loader import load_db_json
        from claims_parser.db_mapper import resolve_form

        db_path = Path(args.db_json)
        if not db_path.exists():
            print(f"missing: {db_path}", file=sys.stderr)
            return 1
        fillmap_path = Path(args.fillmap) if args.fillmap else INTERMEDIATE_DIR / f"{stem}.fillmap.json"

        print(f"-> Loading DB:     {db_path}")
        db = load_db_json(db_path)

        print(f"-> Resolving (fillmap={fillmap_path}, refresh={args.refresh_fillmap})...")
        fmap = resolve_form(
            schema, db,
            fillmap_path=fillmap_path,
            pdf_basename=stem,
            model=args.model,
            refresh=args.refresh_fillmap,
        )
        for note in fmap.notes:
            print(f"   note: {note}")
        if fmap.unresolved_field_ids:
            print(f"   unresolved ({len(fmap.unresolved_field_ids)}): {fmap.unresolved_field_ids[:10]}"
                  + (" ..." if len(fmap.unresolved_field_ids) > 10 else ""))

        print("-> Materializing...")
        filled = fill_from_db(schema, fmap, db)
    else:
        print(f"-> Filling with {args.model} (LLM)...")
        filled = fill_form_schema(schema, model=args.model)

    save_filled_schema(filled, output_path)
    print(f"   wrote {output_path}")
    missing = [f.field_id for f in filled.fields if f.value in (None, "", [])]
    print(f"   Filled: {len(filled.fields) - len(missing)}/{len(filled.fields)}")
    if missing:
        print(f"   Empty:  {missing[:10]}" + (" ..." if len(missing) > 10 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
