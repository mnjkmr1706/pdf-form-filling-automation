"""CLI: enumerate AcroForm widgets from a PDF (mapped branch).

Usage:
    python widget_extract.py input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf
    python widget_extract.py <pdf> -o <widgets.json>
"""
import argparse
import sys
from pathlib import Path

from claims_parser.widget_extractor import extract_widget_catalog, save_catalog

OUT_DIR = Path("output/intermediate/mapped")


def main() -> int:
    p = argparse.ArgumentParser(description="Extract AcroForm widget catalog (mapped branch).")
    p.add_argument("pdf")
    p.add_argument("-o", "--output", default=None,
                   help="Output path (default: output/intermediate/mapped/<stem>.widgets.json).")
    args = p.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"✗ PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else OUT_DIR / f"{pdf_path.stem}.widgets.json"

    print(f"→ Extracting widgets from {pdf_path.name}...")
    catalog = extract_widget_catalog(pdf_path)
    save_catalog(catalog, output_path)

    print(f"✓ Saved widgets → {output_path}")
    print(f"  Pages:   {catalog.page_count}")
    print(f"  Widgets: {len(catalog.widgets)}")

    from collections import Counter
    by_type = Counter(w.widget_type for w in catalog.widgets)
    for t, c in by_type.most_common():
        print(f"    {t:10s} {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
