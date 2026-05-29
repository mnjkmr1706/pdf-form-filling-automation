"""CLI: bind AcroForm widgets to printed labels using a vision LLM.

Reads a cached DocumentContext + WidgetCatalog, calls the vision mapper, and
emits both the raw mapping and the schema-with-bindings consumed by
fill_schema.py.

Usage:
    python map_widgets.py input/ANTHEM_NV_CAID_ClaimsAppealsForm.pdf \
        output/intermediate/ANTHEM_NV_CAID_ClaimsAppealsForm.context.json \
        output/intermediate/mapped/ANTHEM_NV_CAID_ClaimsAppealsForm.widgets.json
"""
import argparse
import sys
from pathlib import Path

from claims_parser.extractor import load_context
from claims_parser.schema_builder import save_schema
from claims_parser.widget_extractor import load_catalog
from claims_parser.widget_mapper import (
    DEFAULT_MODEL,
    build_schema_from_mapping,
    map_widgets,
    save_mapping,
)

OUT_DIR = Path("output/intermediate/mapped")


def main() -> int:
    p = argparse.ArgumentParser(description="LLM-based widget-to-label binding (mapped branch).")
    p.add_argument("pdf")
    p.add_argument("context", help="DocumentContext JSON produced by extract.py.")
    p.add_argument("widgets", help="WidgetCatalog JSON produced by widget_extract.py.")
    p.add_argument("-m", "--model", default=DEFAULT_MODEL)
    p.add_argument("--mapping-out", default=None,
                   help="Where to save the mapping (default: output/intermediate/mapped/<stem>.mapping.json).")
    p.add_argument("--schema-out", default=None,
                   help="Where to save the schema (default: output/intermediate/mapped/<stem>.schema.json).")
    args = p.parse_args()

    pdf_path = Path(args.pdf)
    ctx_path = Path(args.context)
    cat_path = Path(args.widgets)
    for p_ in (pdf_path, ctx_path, cat_path):
        if not p_.exists():
            print(f"✗ Not found: {p_}", file=sys.stderr)
            return 1

    stem = pdf_path.stem
    mapping_out = Path(args.mapping_out) if args.mapping_out else OUT_DIR / f"{stem}.mapping.json"
    schema_out = Path(args.schema_out) if args.schema_out else OUT_DIR / f"{stem}.schema.json"

    print(f"→ Loading context: {ctx_path.name}")
    doc_ctx = load_context(ctx_path)
    print(f"  {len(doc_ctx.lines)} lines, {len(doc_ctx.key_value_pairs)} KVPs, "
          f"{len(doc_ctx.tables)} tables, {len(doc_ctx.selection_marks)} marks")

    print(f"→ Loading widgets: {cat_path.name}")
    catalog = load_catalog(cat_path)
    print(f"  {len(catalog.widgets)} widgets over {catalog.page_count} page(s)")

    print(f"→ Mapping with {args.model} (vision)...")
    mapping = map_widgets(pdf_path, doc_ctx, catalog, model=args.model)
    save_mapping(mapping, mapping_out)
    print(f"✓ Saved mapping → {mapping_out}")
    print(f"  bindings:  {len(mapping.bindings)}")
    print(f"  unmapped:  {len(mapping.unmapped_widget_field_names)}")
    print(f"  chunks:    {mapping.chunk_count}")

    schema = build_schema_from_mapping(catalog, mapping)
    save_schema(schema, schema_out)
    print(f"✓ Saved schema  → {schema_out}")
    print(f"  fields:    {len(schema.fields)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
