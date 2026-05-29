"""CLI: build a structured FormSchema from a cached DocumentContext using Agent 1.

Loads a previously-extracted context JSON, calls the LLM, and saves the schema.

Usage:
    python build_schema.py output/intermediate/AETNA_Form1.context.json
    python build_schema.py <context.json> -o <schema.json> -m gpt-5-mini
"""
import argparse
import sys
from pathlib import Path

from claims_parser import build_form_schema, load_context, save_schema

INTERMEDIATE_DIR = Path("output/intermediate")


def main() -> int:
    p = argparse.ArgumentParser(description="Build a form schema from a cached document context.")
    p.add_argument("context", help="Path to the DocumentContext JSON produced by extract.py.")
    p.add_argument(
        "-o", "--output",
        default=None,
        help="Where to save the schema JSON (default: output/intermediate/<stem>.schema.json).",
    )
    p.add_argument(
        "-m", "--model",
        default="gpt-5-mini",
        help="OpenAI model to use (default: gpt-5-mini).",
    )
    args = p.parse_args()

    ctx_path = Path(args.context)
    if not ctx_path.exists():
        print(f"✗ Context file not found: {ctx_path}", file=sys.stderr)
        return 1

    if args.output:
        output_path = Path(args.output)
    else:
        stem = ctx_path.name
        for suf in (".context.json", ".json"):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
                break
        output_path = INTERMEDIATE_DIR / f"{stem}.schema.json"

    print(f"→ Loading context: {ctx_path}")
    doc_ctx = load_context(ctx_path)
    print(f"  {len(doc_ctx.lines)} lines, {len(doc_ctx.key_value_pairs)} KVPs, "
          f"{len(doc_ctx.tables)} tables")

    print(f"→ Building schema with {args.model}...")
    schema = build_form_schema(doc_ctx, model=args.model)
    save_schema(schema, output_path)

    print(f"✓ Saved schema → {output_path}")
    print()
    print(f"  Form title:    {schema.form_title}")
    print(f"  Issuer:        {schema.issuer}")
    print(f"  Sections:      {len(schema.sections)}")
    print(f"  Total fields:  {len(schema.fields)}")

    if schema.fields:
        from collections import Counter
        by_type = Counter(f.field_type for f in schema.fields)
        print(f"  By type:")
        for t, c in by_type.most_common():
            print(f"    {t:14s} {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
