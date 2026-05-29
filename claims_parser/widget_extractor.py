"""Enumerate AcroForm widgets from a PDF and produce a WidgetCatalog.

Pure pymupdf — no LLM, no Azure. Each radio-group option is a distinct widget.
"""
from pathlib import Path

import pymupdf

from claims_parser.widget_models import Widget, WidgetCatalog, WidgetRect, WidgetType


_TYPE_MAP: dict[str, WidgetType] = {
    "text": "text",
    "checkbox": "checkbox",
    "radiobutton": "radio",
    "listbox": "choice",
    "combobox": "choice",
    "signature": "signature",
    "pushbutton": "button",
}


def _classify(widget) -> WidgetType:
    raw = (widget.field_type_string or "").lower()
    return _TYPE_MAP.get(raw, "text")


def _on_value(widget) -> str | None:
    try:
        v = widget.on_state()
    except Exception:
        v = None
    return v or None


def extract_widget_catalog(pdf_path: str | Path) -> WidgetCatalog:
    pdf_path = Path(pdf_path)
    doc = pymupdf.open(pdf_path)
    try:
        page_sizes: list[tuple[float, float]] = []
        widgets: list[Widget] = []
        for page_idx, page in enumerate(doc, start=1):
            page_sizes.append((page.rect.width, page.rect.height))
            for w in page.widgets() or []:
                wt = _classify(w)
                r = w.rect
                widgets.append(Widget(
                    field_name=w.field_name or f"_unnamed_{w.xref}",
                    widget_type=wt,
                    rect=WidgetRect(page=page_idx, x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1),
                    xref=w.xref,
                    tu_label=(w.field_label or None),
                    choice_values=(list(w.choice_values) if getattr(w, "choice_values", None) else None),
                    on_value=_on_value(w) if wt in ("checkbox", "radio") else None,
                ))
        return WidgetCatalog(
            file_name=pdf_path.name,
            page_count=doc.page_count,
            page_sizes_pt=page_sizes,
            widgets=widgets,
        )
    finally:
        doc.close()


def save_catalog(catalog: WidgetCatalog, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(catalog.model_dump_json(indent=2))
    return output_path


def load_catalog(input_path: str | Path) -> WidgetCatalog:
    return WidgetCatalog.model_validate_json(Path(input_path).read_text())
