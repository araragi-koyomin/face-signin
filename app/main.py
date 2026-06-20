import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.database import engine, Base
from app.routers import auth, faces, signin


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    os.makedirs("uploads/faces", exist_ok=True)
    os.makedirs("uploads/signin", exist_ok=True)
    yield
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


@app.get("/")
async def root():
    return RedirectResponse("/app/signin")


@app.get("/app/login")
async def login_page():
    return FileResponse("app/static/login.html")


@app.get("/app/register")
async def register_page():
    return FileResponse("app/static/register.html")


@app.get("/app/signin")
async def signin_page():
    return FileResponse("app/static/signin.html")


@app.get("/app/dashboard")
async def dashboard_page():
    return FileResponse("app/static/dashboard.html")


@app.get("/app/faces")
async def faces_page():
    return FileResponse("app/static/faces.html")


@app.get("/app/records")
async def records_page():
    return FileResponse("app/static/records.html")
