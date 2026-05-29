"""CLI: write values from a FilledFormSchema into an editable AcroForm PDF."""
import argparse
import sys
from pathlib import Path

from claims_parser import (
    load_binding,
    load_filled_schema,
    save_review,
    write_acroform,
)

INTERMEDIATE_DIR = Path("output/intermediate")
FINAL_DIR = Path("output/final")


def main() -> int:
    p = argparse.ArgumentParser(description="Stamp values into an editable AcroForm PDF.")
    p.add_argument("pdf", help="Original (blank) editable PDF.")
    p.add_argument("filled", help="FilledFormSchema JSON (output of fill_schema.py).")
    p.add_argument("-b", "--binding", default=None,
                   help="Binding JSON (default: output/intermediate/<pdf-stem>.binding.json).")
    p.add_argument("-o", "--output", default=None,
                   help="Output PDF path (default: output/final/<pdf-stem>.filled.pdf).")
    p.add_argument("--review-out", default=None,
                   help="Path for the review report (default: output/intermediate/<pdf-stem>.review.json).")
    p.add_argument("--flatten", action="store_true",
                   help="Flatten widgets in the output (default: keep editable).")
    args = p.parse_args()

    pdf_path = Path(args.pdf)
    filled_path = Path(args.filled)
    binding_path = Path(args.binding) if args.binding else INTERMEDIATE_DIR / f"{pdf_path.stem}.binding.json"
    for pth in (pdf_path, filled_path, binding_path):
        if not pth.exists():
            print(f"✗ Not found: {pth}", file=sys.stderr)
            return 1

    output_path = Path(args.output) if args.output else FINAL_DIR / f"{pdf_path.stem}.filled.pdf"
    review_path = Path(args.review_out) if args.review_out else INTERMEDIATE_DIR / f"{pdf_path.stem}.review.json"

    print(f"→ Loading filled schema: {filled_path}")
    filled = load_filled_schema(filled_path)
    print(f"  {len(filled.fields)} fields")

    print(f"→ Loading binding: {binding_path}")
    binding = load_binding(binding_path)
    print(f"  {len(binding.fields)} widget bindings")

    print(f"→ Writing AcroForm values (flatten={args.flatten})...")
    review = write_acroform(pdf_path, filled, binding, output_path, flatten=args.flatten)
    print(f"✓ Saved → {output_path}")

    save_review(review, review_path)
    print(f"✓ Review report → {review_path}")
    counts = review.by_reason()
    if counts:
        print("  flags by reason:")
        for k, v in counts.items():
            print(f"    {k:32s} {v}")
    else:
        print("  no fields flagged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
