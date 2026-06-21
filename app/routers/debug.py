"""
Debug / demo endpoints — show database state for live demonstration.

Usage during defense:
  1. Call GET /api/debug/stats   → note counts
  2. Perform register / sign-in
  3. Call GET /api/debug/stats   → see counts increment
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models.user import User
from app.models.face import Face
from app.models.attendance import Attendance

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Return table row counts and latest records for live demo."""
    user_count = await db.execute(select(func.count()).select_from(User))
    face_count = await db.execute(select(func.count()).select_from(Face))
    att_count = await db.execute(select(func.count()).select_from(Attendance))

    latest_user = await db.execute(
        select(User.username, User.display_name, User.role, User.created_at)
        .order_by(User.created_at.desc()).limit(3)
    )
    latest_att = await db.execute(
        select(Attendance.photo_path, Attendance.confidence, Attendance.timestamp,
               User.display_name)
        .join(User, Attendance.user_id == User.id)
        .order_by(Attendance.timestamp.desc()).limit(5)
    )

    return {
        "counts": {
            "users": user_count.scalar(),
            "faces": face_count.scalar(),
            "attendance": att_count.scalar(),
        },
        "latest_users": [
            {"username": r[0], "display_name": r[1], "role": r[2], "created_at": str(r[3])}
            for r in latest_user.all()
        ],
        "latest_attendance": [
            {"photo_path": r[0], "confidence": r[1], "timestamp": str(r[2]), "display_name": r[3]}
            for r in latest_att.all()
        ],
    }
