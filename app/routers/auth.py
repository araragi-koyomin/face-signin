import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

import httpx
import aiofiles

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.face import Face
from app.schemas.user import UserCreate, UserLogin, UserOut, TokenOut
from app.services.auth import hash_password, verify_password, create_access_token, get_current_user
from app.services.baidu_face import faceset_add_face

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    count_result = await db.execute(select(func.count()).select_from(User))
    total = count_result.scalar()

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role="admin" if total == 0 else "user",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut)
async def login(body: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(str(user.id))
    return TokenOut(access_token=token)


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/register-with-face", status_code=status.HTTP_201_CREATED)
async def register_with_face(
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...),
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only image files allowed")

    contents = await image.read()
    if len(contents) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")

    count_result = await db.execute(select(func.count()).select_from(User))
    total = count_result.scalar()

    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        role="admin" if total == 0 else "user",
    )
    db.add(user)
    await db.flush()

    upload_dir = Path(settings.UPLOAD_DIR) / "faces"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}.jpg"
    filepath = upload_dir / filename

    async with aiofiles.open(filepath, "wb") as f:
        await f.write(contents)

    try:
        import base64
        image_b64 = base64.b64encode(contents).decode("utf-8")
        baidu_user_id = str(user.id).replace("-", "")

        async with httpx.AsyncClient(timeout=30.0) as client:
            baidu_face_token = await faceset_add_face(
                client, settings.BAIDU_GROUP_ID, image_b64, baidu_user_id
            )

        face = Face(
            user_id=user.id,
            baidu_face_token=baidu_face_token,
            baidu_group_id=settings.BAIDU_GROUP_ID,
            image_path=str(filepath),
        )
        db.add(face)
        await db.commit()
        await db.refresh(user)

        token = create_access_token(str(user.id))

        return {
            "user": UserOut.model_validate(user),
            "face_token": baidu_face_token,
            "access_token": token,
            "token_type": "bearer",
            "message": "注册成功，人脸已录入",
        }
    except Exception as e:
        await db.rollback()
        if filepath.exists():
            filepath.unlink()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
