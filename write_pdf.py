"""CLI: stamp values from a filled schema onto the original PDF.

Pipeline:
  1. Load DocumentContext + FilledFormSchema.
  2. anchor_placer.build_initial_plan          deterministic
  3. pdf_writer.correct_plan                   N vision passes (default 2)
  4. pdf_writer.apply_plan                     stamp final PDF
  5. write a <stem>.review.json flagging fields that need human review
"""
import argparse
import sys
from pathlib import Path

from claims_parser import (
    ReviewItem,
    ReviewReport,
    apply_plan,
    build_initial_plan,
    correct_plan,
    load_context,
    load_filled_schema,
    save_plan,
    save_review,
)

INTERMEDIATE_DIR = Path("output/intermediate")
FINAL_DIR = Path("output/final")


def _default_context_path(pdf_path: Path) -> Path:
    return INTERMEDIATE_DIR / f"{pdf_path.stem}.context.json"


def main() -> int:
    p = argparse.ArgumentParser(description="Write a filled schema onto its source PDF.")
    p.add_argument("pdf", help="Path to the original (blank) PDF.")
    p.add_argument("filled", help="Path to the FilledFormSchema JSON.")
    p.add_argument("-c", "--context", default=None,
                   help="Path to the DocumentContext JSON (default: <pdf-stem>.context.json).")
    p.add_argument("-o", "--output", default=None,
                   help="Output PDF path (default: output/final/<pdf-stem>.filled.pdf).")
    p.add_argument("--plan-out", default=None,
                   help="Optional path to also save the final WritePlan JSON (default: output/intermediate/<stem>.writeplan.json).")
    p.add_argument("--review-out", default=None,
                   help="Path for the human-review report (default: output/intermediate/<pdf-stem>.review.json).")
    p.add_argument("--iterations", type=int, default=2,
                   help="Number of vision correction passes (default: 2). Use 0 to skip.")
    p.add_argument("-m", "--model", default="gpt-5-mini")
    p.add_argument("--font-size", type=float, default=10.0)
    args = p.parse_args()

    pdf_path = Path(args.pdf)
    filled_path = Path(args.filled)
    ctx_path = Path(args.context) if args.context else _default_context_path(pdf_path)
    for pth in (pdf_path, filled_path, ctx_path):
        if not pth.exists():
            print(f"✗ Not found: {pth}", file=sys.stderr)
            return 1

    output_path = Path(args.output) if args.output else FINAL_DIR / f"{pdf_path.stem}.filled.pdf"
    review_path = Path(args.review_out) if args.review_out else INTERMEDIATE_DIR / f"{pdf_path.stem}.review.json"
    plan_out_path = (
        Path(args.plan_out) if args.plan_out
        else INTERMEDIATE_DIR / f"{pdf_path.stem}.writeplan.json"
    )

    print(f"→ Loading context: {ctx_path}")
    ctx = load_context(ctx_path)
    print(f"  {len(ctx.lines)} lines, {len(ctx.key_value_pairs)} KVPs, "
          f"{len(ctx.selection_marks)} selection marks")

    print(f"→ Loading filled schema: {filled_path}")
    filled = load_filled_schema(filled_path)
    print(f"  {len(filled.fields)} fields")

    label_by_id = {f.field_id: f.label for f in filled.fields}
    section_by_id = {f.field_id: f.section for f in filled.fields}

    review_items: list[ReviewItem] = []
    for f in filled.fields:
        if f.value in (None, "", []):
            review_items.append(ReviewItem(
                field_id=f.field_id, label=f.label, section=f.section,
                reason="missing_value",
            ))

    print("→ Anchor placement...")
    plan, unanchored = build_initial_plan(filled, ctx)
    print(f"  {len(plan.operations)} ops placed; {len(unanchored)} fields unanchored")
    for fid in unanchored:
        review_items.append(ReviewItem(
            field_id=fid,
            label=label_by_id.get(fid),
            section=section_by_id.get(fid),
            reason="unanchored",
        ))

    page_sizes: list[tuple[int, int]] = []
    if args.iterations > 0 and plan.operations:
        print(f"→ Vision correction × {args.iterations} ({args.model})...")
        plan, page_sizes, vision_report = correct_plan(
            plan, pdf_path, model=args.model, iterations=args.iterations,
        )
        for it in vision_report.items:
            it.label = label_by_id.get(it.field_id)
            it.section = section_by_id.get(it.field_id)
        review_items.extend(vision_report.items)
        print(f"  done; {len(vision_report.items)} markers flagged by vision")

    save_plan(plan, plan_out_path)
    print(f"  plan saved → {plan_out_path}")

    print("→ Stamping PDF...")
    apply_plan(plan, page_sizes, pdf_path, output_path, font_size=args.font_size)
    print(f"✓ Saved → {output_path}")

    report = ReviewReport(items=review_items)
    save_review(report, review_path)
    print(f"✓ Review report → {review_path}")
    counts = report.by_reason()
    if counts:
        print("  flags by reason:")
        for k, v in counts.items():
            print(f"    {k:32s} {v}")
    else:
        print("  no fields flagged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
