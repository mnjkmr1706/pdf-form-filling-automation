"""CLI: fill empty fields in a canonical DB JSON template with the LLM.

Usage:
    python fill_db.py database/database_schema.json
    python fill_db.py database/database_schema.json -o database/case-1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from claims_parser.db_template_filler import (
    fill_db_template,
    load_db_envelope,
    save_filled_db,
)


def _default_output(src: Path) -> Path:
    name = src.name
    if name.endswith(".json"):
        return src.with_name(name[:-5] + ".filled.json")
    return src.with_name(name + ".filled.json")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Fill empty fields in a canonical DB JSON template using the LLM.",
    )
    p.add_argument("template", help="Path to a DB JSON file (envelope-wrapped, with possibly empty fields).")
    p.add_argument("-o", "--output", default=None,
                   help="Where to save the filled DB JSON (default: alongside input as <stem>.filled.json).")
    p.add_argument("-m", "--model", default="gpt-5-mini")
    args = p.parse_args()

    src = Path(args.template)
    if not src.exists():
        print(f"missing: {src}", file=sys.stderr)
        return 1

    output = Path(args.output) if args.output else _default_output(src)

    print(f"-> Loading template: {src}")
    template = load_db_envelope(src)

    print(f"-> Filling with {args.model}...")
    filled = fill_db_template(template, model=args.model)
    save_filled_db(filled, output)
    print(f"   wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
