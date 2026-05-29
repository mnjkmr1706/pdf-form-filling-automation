from typing import Literal, Optional

from pydantic import BaseModel, Field


class WriteOp(BaseModel):
    field_id: str = Field(description="The field_id from the filled schema this op writes for.")
    page: int = Field(description="1-indexed page number.")
    kind: Literal["text", "tick"] = Field(
        default="text",
        description=(
            "'text' draws op.text at baseline (op.x, op.y). "
            "'tick' draws a checkmark centered at (op.x, op.y); op.text is ignored."
        ),
    )
    text: str = Field(
        default="",
        description="The literal text to draw for kind='text'. Ignored for kind='tick'.",
    )
    x: float = Field(description="X coordinate in image pixels, top-left origin.")
    y: float = Field(description="Y coordinate in image pixels, top-left origin.")
    max_width: Optional[float] = Field(
        default=None,
        description="Optional horizontal space in pixels available for the text. Null if not constrained.",
    )


class WritePlan(BaseModel):
    operations: list[WriteOp]
