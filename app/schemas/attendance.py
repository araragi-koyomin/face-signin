from pydantic import BaseModel
from datetime import datetime, date
from uuid import UUID


class SignInResponse(BaseModel):
    success: bool
    user_id: UUID | None = None
    display_name: str | None = None
    confidence: float | None = None
    message: str


class AttendanceOut(BaseModel):
    id: UUID
    user_id: UUID
    face_id: UUID | None
    photo_path: str
    confidence: float
    timestamp: datetime
    display_name: str | None = None

    model_config = {"from_attributes": True}


class AttendanceStats(BaseModel):
    date: date
    total: int
    signed_in: int
    absent: int
