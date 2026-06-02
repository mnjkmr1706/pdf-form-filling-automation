"""CLI: resolve a FormSchema against a DB JSON. Writes <stem>.fillmap.json.

Cached and idempotent: re-running on the same schema + DB schema fingerprint
returns the existing fillmap without invoking the LLM. Pass --refresh to
force re-resolution.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from claims_parser import load_schema
from claims_parser.db_loader import load_db_json
from claims_parser.db_mapper import resolve_form

INTERMEDIATE_DIR = Path("output/intermediate")


def _strip_schema_suffix(name: str) -> str:
    for suf in (".schema.json", ".json"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def main() -> int:
    p = argparse.ArgumentParser(
        description="Resolve a FormSchema against a DB JSON. Writes <stem>.fillmap.json.",
    )
    p.add_argument("schema", help="Path to the FormSchema JSON.")
    p.add_argument("db_json", help="Path to the DB JSON (envelope-wrapped).")
    p.add_argument("-o", "--output", default=None,
                   help="Where to save the fillmap (default: output/intermediate/<stem>.fillmap.json).")
    p.add_argument("-m", "--model", default="gpt-5-mini")
    p.add_argument("--refresh", action="store_true",
                   help="Ignore the existing cache and re-resolve.")
    args = p.parse_args()

    schema_path = Path(args.schema)
    db_path = Path(args.db_json)
    for p_ in (schema_path, db_path):
        if not p_.exists():
            print(f"missing: {p_}", file=sys.stderr)
            return 1

    stem = _strip_schema_suffix(schema_path.name)
    output_path = Path(args.output) if args.output else INTERMEDIATE_DIR / f"{stem}.fillmap.json"

    print(f"-> Loading schema: {schema_path}")
    schema = load_schema(schema_path)
    print(f"   {len(schema.fields)} fields")

    print(f"-> Loading DB:     {db_path}")
    db = load_db_json(db_path)

    print(f"-> Resolving (model={args.model}, refresh={args.refresh})...")
    fmap = resolve_form(
        schema, db,
        fillmap_path=output_path,
        pdf_basename=stem,
        model=args.model,
        refresh=args.refresh,
    )

    by_conf: dict[str, int] = {}
    for fr in fmap.field_resolutions:
        by_conf[fr.confidence] = by_conf.get(fr.confidence, 0) + 1
    print(f"   wrote {output_path}")
    print(f"   confidence: {by_conf}")
    if fmap.unresolved_field_ids:
        print(f"   unresolved ({len(fmap.unresolved_field_ids)}): {fmap.unresolved_field_ids[:10]}"
              + (" ..." if len(fmap.unresolved_field_ids) > 10 else ""))
    for note in fmap.notes:
        print(f"   note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
