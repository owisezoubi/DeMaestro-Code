"""Pydantic v2 models for raw requirement inputs."""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class RawInputType(str, Enum):
    text = "text"
    pdf = "pdf"


class RawInputCreate(BaseModel):
    """Payload for incoming text submissions."""
    content: str = Field(..., max_length=50_000)


class RawInputDoc(BaseModel):
    """Shape of a raw_inputs document in Firestore."""
    id: str
    type: RawInputType
    content: Optional[str] = None
    storage_ref: Optional[str] = None
    extracted_text: Optional[str] = None
    char_count: int
    timestamp: datetime

    @model_validator(mode="after")
    def _check_content_xor_storage_ref(self) -> "RawInputDoc":
        if self.type == RawInputType.text and self.content is None:
            raise ValueError("content is required for type=text")
        if self.type == RawInputType.pdf and self.storage_ref is None:
            raise ValueError("storage_ref is required for type=pdf")
        return self
