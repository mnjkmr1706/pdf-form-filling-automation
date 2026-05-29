from claims_parser.extractor import extract_document_context, save_context, load_context
from claims_parser.models import (
    DocumentContext,
    PageMetadata,
    TextLine,
    Table,
    TableCell,
    KVP,
    SelectionMark,
)
from claims_parser.schema_builder import build_form_schema, save_schema, load_schema
from claims_parser.schema_models import FormField, FormSchema, FieldType
from claims_parser.form_filler import fill_form_schema, save_filled_schema, load_filled_schema
from claims_parser.filler_models import FilledFormField, FilledFormSchema
from claims_parser.anchor_placer import build_initial_plan
from claims_parser.pdf_writer import correct_plan, apply_plan, save_plan, load_plan, save_review
from claims_parser.writer_models import WriteOp, WritePlan
from claims_parser.review_models import ReviewItem, ReviewReport
from claims_parser.acroform_detect import is_acroform_pdf, pick_branch
from claims_parser.acroform_models import (
    AcroFormBinding,
    AcroFormFieldBinding,
    OptionBinding,
)
from claims_parser.acroform_extractor import (
    extract_acroform,
    save_binding,
    load_binding,
)
from claims_parser.acroform_writer import write_acroform
from claims_parser.widget_models import Widget, WidgetCatalog, WidgetRect, WidgetType
from claims_parser.widget_extractor import (
    extract_widget_catalog,
    save_catalog,
    load_catalog,
)
from claims_parser.mapping_models import WidgetBinding, WidgetMapping, LabelSource
from claims_parser.widget_mapper import (
    map_widgets,
    build_schema_from_mapping,
    save_mapping,
    load_mapping,
)
from claims_parser.mapped_writer import write_mapped

__all__ = [
    # extraction
    "extract_document_context",
    "save_context",
    "load_context",
    "DocumentContext",
    "PageMetadata",
    "TextLine",
    "Table",
    "TableCell",
    "KVP",
    "SelectionMark",
    # schema building (Agent 1)
    "build_form_schema",
    "save_schema",
    "load_schema",
    "FormField",
    "FormSchema",
    "FieldType",
    # form filling (Agent 2)
    "fill_form_schema",
    "save_filled_schema",
    "load_filled_schema",
    "FilledFormField",
    "FilledFormSchema",
    # pdf writing (Agent 3)
    "build_initial_plan",
    "correct_plan",
    "apply_plan",
    "save_plan",
    "load_plan",
    "WriteOp",
    "WritePlan",
    # review
    "save_review",
    "ReviewItem",
    "ReviewReport",
    # acroform branch
    "is_acroform_pdf",
    "pick_branch",
    "AcroFormBinding",
    "AcroFormFieldBinding",
    "OptionBinding",
    "extract_acroform",
    "save_binding",
    "load_binding",
    "write_acroform",
    # mapped acroform branch
    "Widget",
    "WidgetCatalog",
    "WidgetRect",
    "WidgetType",
    "extract_widget_catalog",
    "save_catalog",
    "load_catalog",
    "WidgetBinding",
    "WidgetMapping",
    "LabelSource",
    "map_widgets",
    "build_schema_from_mapping",
    "save_mapping",
    "load_mapping",
    "write_mapped",
]
