"""CLI: fill a FormSchema with plausible dummy values using Agent 2."""
import argparse
import sys
from pathlib import Path

from claims_parser import fill_form_schema, load_schema, save_filled_schema

INTERMEDIATE_DIR = Path("output/intermediate")


def main() -> int:
    p = argparse.ArgumentParser(description="Fill a form schema with plausible values.")
    p.add_argument("schema", help="Path to the FormSchema JSON produced by build_schema.py.")
    p.add_argument("-o", "--output", default=None,
                   help="Where to save the filled schema (default: output/intermediate/<stem>.filled.json).")
    p.add_argument("-m", "--model", default="gpt-5-mini")
    args = p.parse_args()

    schema_path = Path(args.schema)
    if not schema_path.exists():
        print(f"✗ Schema file not found: {schema_path}", file=sys.stderr)
        return 1

    if args.output:
        output_path = Path(args.output)
    else:
        stem = schema_path.name
        for suf in (".schema.json", ".json"):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
                break
        output_path = INTERMEDIATE_DIR / f"{stem}.filled.json"

    print(f"→ Loading schema: {schema_path}")
    schema = load_schema(schema_path)
    print(f"  {len(schema.fields)} fields, {len(schema.sections)} sections")

    print(f"→ Filling with {args.model}...")
    filled = fill_form_schema(schema, model=args.model)
    save_filled_schema(filled, output_path)

    print(f"✓ Saved → {output_path}")
    missing = [f.field_id for f in filled.fields if f.value in (None, "", [])]
    print(f"  Filled: {len(filled.fields) - len(missing)}/{len(filled.fields)}")
    if missing:
        print(f"  Empty:  {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
