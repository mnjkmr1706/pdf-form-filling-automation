from typing import Optional, Union

from pydantic import BaseModel, Field

from claims_parser.schema_models import FormField, FormSchema


class FilledFormField(FormField):
    value: Union[str, list[str], None] = Field(
        description=(
            "The value to write into this field. "
            "For radio_group: exactly one string from options. "
            "For checkbox: a list of zero or more strings from options. "
            "For every other field_type: a single string consistent with field_type and format_hint. "
            "Null only if no plausible value can be produced."
        )
    )


class FilledFormSchema(BaseModel):
    form_title: Optional[str]
    issuer: Optional[str]
    sections: list[str]
    fields: list[FilledFormField]

    @classmethod
    def from_schema(cls, schema: FormSchema, filled_fields: list[FilledFormField]) -> "FilledFormSchema":
        return cls(
            form_title=schema.form_title,
            issuer=schema.issuer,
            sections=schema.sections,
            fields=filled_fields,
        )
