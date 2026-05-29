"""CLI: map_widgets with a fingerprint-keyed cache.

On a cache hit, no LLM call is made: the cached WidgetMapping + FormSchema
are rebound to the current PDF's widget xrefs and written to the same
output paths as map_widgets.py.

Usage:
    python map_widgets_cached.py input/<form>.pdf \
        output/intermediate/<form>.context.json \
        output/intermediate/mapped/<form>.widgets.json
"""
import argparse
import sys
from pathlib import Path

from claims_parser.extractor import load_context
from claims_parser.mapping_cache import (
    compute_form_fingerprint,
    lookup,
    rebind_to_current_xrefs,
    store,
)
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
    p = argparse.ArgumentParser(description="Mapped AcroForm branch with mapping cache.")
    p.add_argument("pdf")
    p.add_argument("context", help="DocumentContext JSON produced by extract.py.")
    p.add_argument("widgets", help="WidgetCatalog JSON produced by widget_extract.py.")
    p.add_argument("-m", "--model", default=DEFAULT_MODEL)
    p.add_argument("--mapping-out", default=None)
    p.add_argument("--schema-out", default=None)
    p.add_argument("--no-cache", action="store_true",
                   help="Bypass cache entirely: skip read AND skip write.")
    p.add_argument("--rebuild", action="store_true",
                   help="Force re-derivation and overwrite the cache entry.")
    p.add_argument("--strict-cache", action="store_true",
                   help="Exit non-zero on cache miss instead of running the LLM.")
    args = p.parse_args()

    if args.no_cache and args.rebuild:
        print("✗ --no-cache and --rebuild are mutually exclusive", file=sys.stderr)
        return 2

    pdf_path = Path(args.pdf)
    ctx_path = Path(args.context)
    cat_path = Path(args.widgets)
    for p_ in (pdf_path, cat_path):
        if not p_.exists():
            print(f"✗ Not found: {p_}", file=sys.stderr)
            return 1

    stem = pdf_path.stem
    mapping_out = Path(args.mapping_out) if args.mapping_out else OUT_DIR / f"{stem}.mapping.json"
    schema_out = Path(args.schema_out) if args.schema_out else OUT_DIR / f"{stem}.schema.json"

    print(f"→ Loading widgets: {cat_path.name}")
    catalog = load_catalog(cat_path)
    print(f"  {len(catalog.widgets)} widgets over {catalog.page_count} page(s)")

    fingerprint = compute_form_fingerprint(catalog)
    print(f"→ Form fingerprint: {fingerprint}")

    use_cache_read = not (args.no_cache or args.rebuild)
    cached = lookup(catalog) if use_cache_read else None

    if cached is not None:
        rebound = rebind_to_current_xrefs(cached.mapping, cached.form_schema, catalog)
        if rebound is not None:
            mapping, schema = rebound
            mapping_out.parent.mkdir(parents=True, exist_ok=True)
            schema_out.parent.mkdir(parents=True, exist_ok=True)
            save_mapping(mapping, mapping_out)
            save_schema(schema, schema_out)
            print(f"✓ Cache hit (source: {cached.source_pdf_basename}); "
                  f"no LLM call. mapping={mapping_out} schema={schema_out}")
            return 0
        else:
            print("! Cache hit but rebind failed; falling back to LLM",
                  file=sys.stderr)

    if args.strict_cache:
        print("✗ Cache miss with --strict-cache", file=sys.stderr)
        return 2

    if not ctx_path.exists():
        print(f"✗ Not found: {ctx_path}", file=sys.stderr)
        return 1
    print(f"→ Loading context: {ctx_path.name}")
    doc_ctx = load_context(ctx_path)
    print(f"  {len(doc_ctx.lines)} lines, {len(doc_ctx.key_value_pairs)} KVPs, "
          f"{len(doc_ctx.tables)} tables, {len(doc_ctx.selection_marks)} marks")

    print(f"→ Mapping with {args.model} (vision)...")
    mapping = map_widgets(pdf_path, doc_ctx, catalog, model=args.model)
    schema = build_schema_from_mapping(catalog, mapping)
    save_mapping(mapping, mapping_out)
    save_schema(schema, schema_out)
    print(f"✓ Derived fresh mapping. mapping={mapping_out} schema={schema_out}")
    print(f"  bindings: {len(mapping.bindings)}, unmapped: {len(mapping.unmapped_widget_field_names)}, "
          f"chunks: {mapping.chunk_count}")

    if args.no_cache:
        print("→ --no-cache set; skipping cache write")
    else:
        store(fingerprint, mapping, schema, source_pdf_basename=pdf_path.name)
        print(f"✓ Stored cache entry: mappings/{fingerprint}.*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
