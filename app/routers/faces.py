import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import httpx
import aiofiles

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.face import Face
from app.schemas.face import FaceOut, FaceUploadResponse
from app.services.auth import get_current_user, require_admin
from app.services.baidu_face import faceset_add_face, faceset_delete_face, read_image_as_base64

router = APIRouter(prefix="/api/faces", tags=["faces"])


@router.post("", response_model=FaceUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_face(
    user_id: str,
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only image files allowed")

    if current_user.role != "admin" and str(current_user.id) != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only add faces for yourself")

    target = await db.execute(select(User).where(User.id == user_id))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    upload_dir = Path(settings.UPLOAD_DIR) / "faces"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}.jpg"
    filepath = upload_dir / filename

    contents = await image.read()
    if len(contents) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")

    async with aiofiles.open(filepath, "wb") as f:
        await f.write(contents)

    try:
        import base64
        image_b64 = base64.b64encode(contents).decode("utf-8")

        async with httpx.AsyncClient(timeout=30.0) as client:
            baidu_user_id = str(target_user.id).replace("-", "")
            baidu_face_token = await faceset_add_face(
                client, settings.BAIDU_GROUP_ID, image_b64, baidu_user_id
            )

        face = Face(
            user_id=target_user.id,
            baidu_face_token=baidu_face_token,
            baidu_group_id=settings.BAIDU_GROUP_ID,
            image_path=str(filepath),
        )
        db.add(face)
        await db.commit()
        await db.refresh(face)

        return FaceUploadResponse(
            id=face.id,
            user_id=face.user_id,
            baidu_face_token=face.baidu_face_token,
            message="Face registered successfully",
        )
    except Exception as e:
        if filepath.exists():
            filepath.unlink()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("", response_model=list[FaceOut])
async def list_faces(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(Face)
    if current_user.role != "admin":
        query = query.where(Face.user_id == current_user.id)
    result = await db.execute(query.order_by(Face.created_at.desc()))
    return result.scalars().all()


@router.delete("/{face_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_face(
    face_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Face).where(Face.id == face_id))
    face = result.scalar_one_or_none()
    if not face:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Face not found")

    if current_user.role != "admin" and face.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only delete your own faces")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await faceset_delete_face(client, face.baidu_group_id, face.baidu_face_token)
    except Exception:
        pass

    filepath = Path(face.image_path)
    if filepath.exists():
        filepath.unlink()

    await db.delete(face)
    await db.commit()
