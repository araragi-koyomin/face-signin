import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import engine, Base, get_db, init_db
from app.models.user import User
from app.schemas.user import UserOut
from app.services.auth import get_current_user, require_admin
from app.routers import auth, faces, signin
from app.services.baidu_face import close_http_client
from app.concurrency import RateLimiter

NO_CACHE = {"Cache-Control": "no-store, max-age=0"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    os.makedirs("uploads/faces", exist_ok=True)
    os.makedirs("uploads/signin", exist_ok=True)
    yield
    await close_http_client()
    await engine.dispose()


app = FastAPI(
    title="Face Sign-In System",
    description="人脸签到系统 API — 基于百度智能云人脸识别",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(faces.router)
app.include_router(signin.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def enforce_https(request: Request, call_next):
    proto = request.headers.get("X-Forwarded-Proto", "")
    if proto == "http":
        url = str(request.url).replace("http://", "https://", 1)
        return RedirectResponse(url, status_code=301)
    return await call_next(request)


@app.middleware("http")
async def add_no_cache(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path.startswith("/app/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


# ── Per-IP rate limiter (30 req/min for API, 120 req/min for static) ──
_api_limiter = RateLimiter(max_requests=30, window_seconds=60.0)
_static_limiter = RateLimiter(max_requests=120, window_seconds=60.0)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    path = request.url.path

    if path.startswith("/api/"):
        allowed = await _api_limiter.acquire(client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."},
            )
    elif path.startswith("/static/") or path.startswith("/app/"):
        allowed = await _static_limiter.acquire(client_ip)
        if not allowed:
            return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    return await call_next(request)


@app.get("/")
async def root():
    return RedirectResponse("/app/signin")


@app.get("/app/login")
async def login_page():
    return FileResponse("app/static/login.html", headers=NO_CACHE)


@app.get("/app/register")
async def register_page():
    return FileResponse("app/static/register.html", headers=NO_CACHE)


@app.get("/app/signin")
async def signin_page():
    return FileResponse("app/static/signin.html", headers=NO_CACHE)


@app.get("/app/dashboard")
async def dashboard_page():
    return FileResponse("app/static/dashboard.html", headers=NO_CACHE)


@app.get("/app/faces")
async def faces_page():
    return FileResponse("app/static/faces.html", headers=NO_CACHE)


@app.get("/app/records")
async def records_page():
    return FileResponse("app/static/records.html", headers=NO_CACHE)


@app.get("/manifest.json")
async def manifest():
    return FileResponse("app/static/manifest.json")


@app.get("/sw.js")
async def sw():
    return FileResponse("app/static/sw.js")


@app.get("/api/users", response_model=list[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()
