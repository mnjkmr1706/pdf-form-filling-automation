"""Detect whether a PDF carries AcroForm widgets."""
from pathlib import Path
from typing import Literal

import pymupdf


def is_acroform_pdf(pdf_path: str | Path) -> bool:
    doc = pymupdf.open(pdf_path)
    try:
        for page in doc:
            if any(True for _ in page.widgets()):
                return True
        return False
    finally:
        doc.close()


def pick_branch(pdf_path: str | Path) -> Literal["acroform", "scanned"]:
    return "acroform" if is_acroform_pdf(pdf_path) else "scanned"
