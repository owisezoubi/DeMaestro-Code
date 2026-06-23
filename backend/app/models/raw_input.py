"""Pydantic v2 models for raw requirement inputs."""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator
import os


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


class RawInputOut(BaseModel):
    """API response shape for a single raw input."""
    id: str
    type: str
    filename: Optional[str] = None
    content: Optional[str] = None
    extracted_text: Optional[str] = None
    char_count: int
    created_at: datetime

    @classmethod
    def from_doc(cls, doc: "RawInputDoc") -> "RawInputOut":
        filename = None
        if doc.storage_ref:
            filename = os.path.basename(doc.storage_ref)
        return cls(
            id=doc.id,
            type=doc.type.value,
            filename=filename,
            content=doc.content,
            extracted_text=doc.extracted_text,
            char_count=doc.char_count,
            created_at=doc.timestamp,
        )
