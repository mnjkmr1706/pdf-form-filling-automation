"""Azure Document Intelligence extraction.

Calls `prebuilt-layout` with the `keyValuePairs` feature, chunking the PDF into
≤AZURE_CHUNK_PAGES segments to stay under the F0 per-document page cap. Chunk
results are merged with page-number offsets so the returned DocumentContext
covers every page of the source PDF.
"""
from pathlib import Path
from typing import Optional

import pymupdf
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import DocumentAnalysisFeature
from azure.core.credentials import AzureKeyCredential

from claims_parser.config import load_azure_config
from claims_parser.models import (
    DocumentContext,
    KVP,
    PageMetadata,
    Polygon,
    SelectionMark,
    Table,
    TableCell,
    TextLine,
)

AZURE_MODEL = "prebuilt-layout"
AZURE_CHUNK_PAGES = 2  # F0 tier limit


def _polygon_to_list(polygon) -> Polygon:
    if not polygon:
        return []
    first = polygon[0]
    if hasattr(first, "x") and hasattr(first, "y"):
        flat: list[float] = []
        for pt in polygon:
            flat.extend([pt.x, pt.y])
        return flat
    return list(polygon)


def _first_region(regions) -> tuple[Optional[int], Optional[Polygon]]:
    if not regions:
        return None, None
    region = regions[0]
    page = getattr(region, "page_number", None)
    polygon = _polygon_to_list(getattr(region, "polygon", None))
    return page, polygon or None


def _chunk_pdf_bytes(src_path: Path, start: int, end_inclusive: int) -> bytes:
    """Return PDF bytes containing pages [start, end_inclusive] (0-indexed)."""
    sub = pymupdf.open()
    src = pymupdf.open(src_path)
    try:
        sub.insert_pdf(src, from_page=start, to_page=end_inclusive)
        return sub.tobytes()
    finally:
        sub.close()
        src.close()


def _ingest_result(result, page_offset: int) -> tuple[
    list[PageMetadata], list[TextLine], list[Table], list[KVP], list[SelectionMark], str
]:
    pages: list[PageMetadata] = []
    lines: list[TextLine] = []
    selection_marks: list[SelectionMark] = []

    for page in result.pages or []:
        actual = (page.page_number or 1) + page_offset
        pages.append(PageMetadata(
            page_number=actual,
            width=page.width or 0.0,
            height=page.height or 0.0,
            unit=page.unit or "inch",
            line_count=len(page.lines or []),
        ))
        for ln in page.lines or []:
            lines.append(TextLine(
                text=ln.content,
                page=actual,
                polygon=_polygon_to_list(ln.polygon),
            ))
        for mark in getattr(page, "selection_marks", None) or []:
            selection_marks.append(SelectionMark(
                page=actual,
                state=getattr(mark, "state", "unselected") or "unselected",
                polygon=_polygon_to_list(mark.polygon),
                confidence=getattr(mark, "confidence", None),
            ))

    tables: list[Table] = []
    for tbl in result.tables or []:
        table_page, _ = _first_region(getattr(tbl, "bounding_regions", None))
        cells: list[TableCell] = []
        for c in tbl.cells:
            c_page, c_poly = _first_region(getattr(c, "bounding_regions", None))
            cells.append(TableCell(
                row=c.row_index,
                column=c.column_index,
                text=c.content or "",
                page=(c_page or table_page or 1) + page_offset,
                polygon=c_poly,
                row_span=c.row_span or 1,
                column_span=c.column_span or 1,
                kind=getattr(c, "kind", None),
            ))
        tables.append(Table(
            page=(table_page or 1) + page_offset,
            row_count=tbl.row_count,
            column_count=tbl.column_count,
            cells=cells,
        ))

    kvps: list[KVP] = []
    for kvp in result.key_value_pairs or []:
        k_page, k_poly = _first_region(getattr(kvp.key, "bounding_regions", None))
        v_page, v_poly, v_text = None, None, None
        if kvp.value is not None:
            v_page, v_poly = _first_region(getattr(kvp.value, "bounding_regions", None))
            v_text = kvp.value.content
        kvps.append(KVP(
            key_text=kvp.key.content,
            key_page=(k_page + page_offset) if k_page else None,
            key_polygon=k_poly,
            value_text=v_text,
            value_page=(v_page + page_offset) if v_page else None,
            value_polygon=v_poly,
            confidence=getattr(kvp, "confidence", None),
        ))

    return pages, lines, tables, kvps, selection_marks, result.content or ""


def extract_document_context(pdf_path: str | Path) -> DocumentContext:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    cfg = load_azure_config()
    client = DocumentIntelligenceClient(
        endpoint=cfg.endpoint,
        credential=AzureKeyCredential(cfg.key),
    )

    src = pymupdf.open(pdf_path)
    total_pages = src.page_count
    src.close()

    all_pages: list[PageMetadata] = []
    all_lines: list[TextLine] = []
    all_tables: list[Table] = []
    all_kvps: list[KVP] = []
    all_marks: list[SelectionMark] = []
    full_text_parts: list[str] = []

    for chunk_start in range(0, total_pages, AZURE_CHUNK_PAGES):
        chunk_end = min(chunk_start + AZURE_CHUNK_PAGES - 1, total_pages - 1)
        chunk_bytes = _chunk_pdf_bytes(pdf_path, chunk_start, chunk_end)
        print(f"  Azure chunk: pages {chunk_start + 1}-{chunk_end + 1}")

        poller = client.begin_analyze_document(
            model_id=AZURE_MODEL,
            body=chunk_bytes,
            features=[DocumentAnalysisFeature.KEY_VALUE_PAIRS],
            content_type="application/octet-stream",
        )
        result = poller.result()

        pages, lines, tables, kvps, marks, content = _ingest_result(result, chunk_start)
        all_pages.extend(pages)
        all_lines.extend(lines)
        all_tables.extend(tables)
        all_kvps.extend(kvps)
        all_marks.extend(marks)
        full_text_parts.append(content)

    return DocumentContext(
        file_name=pdf_path.name,
        file_path=str(pdf_path),
        model_id=AZURE_MODEL,
        full_text="\n".join(full_text_parts),
        pages=all_pages,
        lines=all_lines,
        tables=all_tables,
        key_value_pairs=all_kvps,
        selection_marks=all_marks,
    )


def save_context(ctx: DocumentContext, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(ctx.model_dump_json(indent=2))
    return output_path


def load_context(input_path: str | Path) -> DocumentContext:
    return DocumentContext.model_validate_json(Path(input_path).read_text())
