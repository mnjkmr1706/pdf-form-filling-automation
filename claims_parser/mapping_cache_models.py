"""Pydantic models for the mapping cache.

Stores a derived WidgetMapping + FormSchema keyed by a form fingerprint so
the vision-LLM step in the mapped AcroForm branch is paid once per blank
form, not once per filled copy.
"""
from pydantic import BaseModel

from claims_parser.mapping_models import WidgetMapping
from claims_parser.schema_models import FormSchema


class CachedMapping(BaseModel):
    form_fingerprint: str
    source_pdf_basename: str
    created_at: str  # ISO-8601 UTC
    mapping: WidgetMapping
    form_schema: FormSchema  # not `schema` — shadows BaseModel.schema() in pydantic v2


class CacheIndexEntry(BaseModel):
    form_fingerprint: str
    source_pdf_basename: str
    created_at: str


class CacheIndex(BaseModel):
    entries: list[CacheIndexEntry] = []
