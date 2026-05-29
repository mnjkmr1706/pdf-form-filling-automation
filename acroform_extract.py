"""CLI: extract a FormSchema + AcroFormBinding directly from an editable PDF.

Produces, under output/intermediate/:
    <stem>.schema.json   (same shape as Agent 1's output; fill_schema.py works on it)
    <stem>.binding.json  (widget map used by acroform_write.py)
"""
import argparse
import sys
from pathlib import Path

from claims_parser import (
    extract_acroform,
    is_acroform_pdf,
    save_binding,
    save_schema,
)

INTERMEDIATE_DIR = Path("output/intermediate")


def main() -> int:
    p = argparse.ArgumentParser(description="Extract schema + binding from an editable AcroForm PDF.")
    p.add_argument("pdf", help="Path to the editable PDF.")
    p.add_argument("--schema-out", default=None,
                   help="Schema output (default: output/intermediate/<stem>.schema.json).")
    p.add_argument("--binding-out", default=None,
                   help="Binding output (default: output/intermediate/<stem>.binding.json).")
    p.add_argument("--no-refine", action="store_true",
                   help="Skip the LLM field-type refinement step.")
    p.add_argument("--no-vision", action="store_true",
                   help="Skip the Tier-3 vision pass for unresolved labels.")
    p.add_argument("-m", "--model", default="gpt-5-mini")
    args = p.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"✗ PDF not found: {pdf_path}", file=sys.stderr)
        return 1
    if not is_acroform_pdf(pdf_path):
        print(f"✗ {pdf_path.name} has no AcroForm widgets. Use extract.py / write_pdf.py instead.",
              file=sys.stderr)
        return 2

    schema_out = Path(args.schema_out) if args.schema_out else INTERMEDIATE_DIR / f"{pdf_path.stem}.schema.json"
    binding_out = Path(args.binding_out) if args.binding_out else INTERMEDIATE_DIR / f"{pdf_path.stem}.binding.json"

    print(f"→ Extracting widgets from {pdf_path.name}...")
    schema, binding = extract_acroform(
        pdf_path,
        refine_types=not args.no_refine,
        use_vision_residuals=not args.no_vision,
        refine_model=args.model,
    )

    save_schema(schema, schema_out)
    save_binding(binding, binding_out)

    print(f"✓ Schema  → {schema_out}")
    print(f"✓ Binding → {binding_out}")
    print()
    print(f"  Fields:        {len(schema.fields)}")
    print(f"  Bindings:      {len(binding.fields)}")
    if schema.fields:
        from collections import Counter
        by_type = Counter(f.field_type for f in schema.fields)
        print(f"  By type:")
        for t, c in by_type.most_common():
            print(f"    {t:14s} {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
