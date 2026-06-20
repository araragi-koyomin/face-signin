import base64
import uuid
from pathlib import Path
from datetime import date

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

import httpx
import aiofiles

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.face import Face
from app.models.attendance import Attendance
from app.schemas.attendance import SignInResponse, AttendanceOut, AttendanceStats
from app.services.auth import get_current_user, require_admin
from app.services.baidu_face import faceset_search

router = APIRouter(prefix="/api", tags=["signin"])


@router.post("/sign-in", response_model=SignInResponse)
async def sign_in(
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only image files allowed")

    contents = await image.read()
    if len(contents) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")

    signin_dir = Path(settings.UPLOAD_DIR) / "signin"
    signin_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}.jpg"
    filepath = signin_dir / filename

    async with aiofiles.open(filepath, "wb") as f:
        await f.write(contents)

    try:
        image_b64 = base64.b64encode(contents).decode("utf-8")

        async with httpx.AsyncClient(timeout=30.0) as client:
            result = await faceset_search(client, settings.BAIDU_GROUP_ID, image_b64)

        user_list = result.get("user_list", [])
        if not user_list:
            return SignInResponse(success=False, message="No matching face found")

        top = user_list[0]
        baidu_user_id = top["user_id"]
        confidence = top["score"]

        user_result = await db.execute(select(User).where(User.id == uuid.UUID(baidu_user_id)))
        user = user_result.scalar_one_or_none()
        if not user:
            return SignInResponse(success=False, message="Matched user not found in system")

        face_result = await db.execute(select(Face).where(Face.user_id == user.id))
        face = face_result.scalar_one_or_none()

        record = Attendance(
            user_id=user.id,
            face_id=face.id if face else None,
            photo_path=str(filepath),
            confidence=confidence,
        )
        db.add(record)
        await db.commit()

        return SignInResponse(
            success=True,
            user_id=user.id,
            display_name=user.display_name,
            confidence=confidence,
            message=f"Sign-in successful: {user.display_name}",
        )
    except RuntimeError as e:
        if filepath.exists():
            filepath.unlink()
        return SignInResponse(success=False, message=str(e))


@router.get("/records", response_model=list[AttendanceOut])
async def get_records(
    target_date: date | None = Query(None, alias="date"),
    user_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(Attendance).options(selectinload(Attendance.user)).order_by(Attendance.timestamp.desc())

    if current_user.role != "admin":
        query = query.where(Attendance.user_id == current_user.id)
    elif user_id:
        query = query.where(Attendance.user_id == user_id)

    if target_date:
        query = query.where(func.date(Attendance.timestamp) == target_date)

    result = await db.execute(query.limit(200))
    records = result.scalars().all()

    out = []
    for r in records:
        out.append(
            AttendanceOut(
                id=r.id,
                user_id=r.user_id,
                face_id=r.face_id,
                photo_path=r.photo_path,
                confidence=r.confidence,
                timestamp=r.timestamp,
                display_name=r.user.display_name if r.user else None,
            )
        )
    return out
