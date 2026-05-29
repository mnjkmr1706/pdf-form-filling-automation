"""CLI: extract document context from a PDF using Azure Document Intelligence.

Usage:
    python extract.py input/AETNA_Form1.pdf
    python extract.py input/AETNA_Form1.pdf -o cache/aetna1.json
"""
import argparse
import sys
from pathlib import Path

from claims_parser import extract_document_context, save_context

INTERMEDIATE_DIR = Path("output/intermediate")


def main() -> int:
    p = argparse.ArgumentParser(description="Extract document context using Azure Doc Intelligence.")
    p.add_argument("pdf", help="Path to the PDF file.")
    p.add_argument(
        "-o", "--output",
        default=None,
        help="Where to save the context JSON (default: output/intermediate/<pdf-stem>.context.json).",
    )
    args = p.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"✗ PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    output_path = (
        Path(args.output)
        if args.output
        else INTERMEDIATE_DIR / f"{pdf_path.stem}.context.json"
    )

    print(f"→ Extracting {pdf_path.name} with Azure Document Intelligence...")
    ctx = extract_document_context(pdf_path)
    save_context(ctx, output_path)

    print(f"✓ Saved context → {output_path}")
    print()
    print(f"  Model:           {ctx.model_id}")
    print(f"  Text length:     {len(ctx.full_text)} chars")
    print(f"  Pages:           {len(ctx.pages)}")
    print(f"  Lines:           {len(ctx.lines)}")
    print(f"  Tables:          {len(ctx.tables)}")
    print(f"  Key-value pairs: {len(ctx.key_value_pairs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
