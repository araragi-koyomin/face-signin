from pydantic import BaseModel
from datetime import datetime
from uuid import UUID


class FaceOut(BaseModel):
    id: UUID
    user_id: UUID
    baidu_face_token: str
    baidu_group_id: str
    image_path: str
    created_at: datetime

    model_config = {"from_attributes": True}


class FaceUploadResponse(BaseModel):
    id: UUID
    user_id: UUID
    baidu_face_token: str
    message: str
