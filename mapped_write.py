"""CLI: write filled values into an AcroForm PDF (mapped branch).

Usage:
    python mapped_write.py input/X.pdf output/intermediate/mapped/X.filled.json
    python mapped_write.py <pdf> <filled.json> -o <out.pdf> --flatten
"""
import argparse
import sys
from pathlib import Path

from claims_parser.filler_models import FilledFormSchema
from claims_parser.mapped_writer import write_mapped
from claims_parser.review_models import ReviewReport

OUT_PDF_DIR = Path("output/final/mapped")
OUT_REVIEW_DIR = Path("output/intermediate/mapped")


def main() -> int:
    p = argparse.ArgumentParser(description="Apply a filled mapped schema to its source PDF.")
    p.add_argument("pdf")
    p.add_argument("filled", help="FilledFormSchema JSON produced by fill_schema.py.")
    p.add_argument("-o", "--output", default=None,
                   help="Output PDF path (default: output/final/mapped/<stem>.filled.pdf).")
    p.add_argument("--review-out", default=None,
                   help="Review JSON path (default: output/intermediate/mapped/<stem>.review.json).")
    p.add_argument("--flatten", action="store_true",
                   help="Bake widgets into static content (PDF becomes non-editable).")
    args = p.parse_args()

    pdf_path = Path(args.pdf)
    filled_path = Path(args.filled)
    for p_ in (pdf_path, filled_path):
        if not p_.exists():
            print(f"✗ Not found: {p_}", file=sys.stderr)
            return 1

    stem = pdf_path.stem
    out_pdf = Path(args.output) if args.output else OUT_PDF_DIR / f"{stem}.filled.pdf"
    out_review = Path(args.review_out) if args.review_out else OUT_REVIEW_DIR / f"{stem}.review.json"

    print(f"→ Loading filled schema: {filled_path.name}")
    filled = FilledFormSchema.model_validate_json(filled_path.read_text())
    print(f"  {len(filled.fields)} fields")

    print(f"→ Writing into {pdf_path.name}...")
    review = write_mapped(pdf_path, filled, out_pdf, flatten=args.flatten)

    out_review.parent.mkdir(parents=True, exist_ok=True)
    out_review.write_text(review.model_dump_json(indent=2))

    print(f"✓ Saved PDF    → {out_pdf}")
    print(f"✓ Saved review → {out_review}")
    print(f"  Review items: {len(review.items)}")
    for reason, count in sorted(review.by_reason().items()):
        print(f"    {reason:24s} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
