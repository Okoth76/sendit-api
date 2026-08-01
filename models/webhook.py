from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field

class Webhook(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    url: str
    event_type: str  # e.g., "document.enriched", "document.uploaded"
    secret: str       # Signature key for validation
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)