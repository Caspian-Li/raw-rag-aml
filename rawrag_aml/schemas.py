from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

Identifier = Annotated[StrictStr, Field(min_length=1, max_length=2048)]


class TextMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: StrictStr
    timestamp: Annotated[StrictInt, Field(ge=-62135596800000, le=253402300799999)] | None = None

    @field_validator("content")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("content must be nonempty")
        return value  # Preserve original whitespace and text.


class AddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Identifier
    user_id: Identifier
    session_id: Identifier
    messages: Annotated[list[TextMessage], Field(min_length=1)]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: StrictStr
    user_id: Identifier
    top_k: Annotated[StrictInt, Field(ge=1, le=100)]
    options: list[StrictStr] | None = None

    @field_validator("query")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("query must be nonempty")
        return value
