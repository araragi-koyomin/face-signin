import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger("uvicorn.error")

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    connect_args={"timeout": 10},
)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db(retries: int = 10, delay: float = 3.0):
    """Connect to database with retries, useful when DB takes time to start."""
    for i in range(retries):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database connected and tables created")
            return
        except Exception as e:
            logger.warning(f"DB connection attempt {i + 1}/{retries} failed: {e}")
            if i < retries - 1:
                await asyncio.sleep(delay)
    raise RuntimeError("Could not connect to database after multiple retries")


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
