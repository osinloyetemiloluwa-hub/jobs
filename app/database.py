from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import settings
from app.models import Base

def _normalize(raw: str):
    url = raw.strip().strip("'").strip('"')
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    connect_args = {}
    if "neon.tech" in url or "sslmode=require" in url.lower():
        connect_args["ssl"] = "require"
    for p in ("sslmode=", "channel_binding="):
        if p in url:
            url = url.split(p)[0].rstrip("?&")
    return url, connect_args

_url, _args = _normalize(settings.database_url)
engine = create_async_engine(_url, pool_pre_ping=True, pool_size=1, max_overflow=2, connect_args=_args)
Session = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)