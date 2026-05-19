from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings


def _make_engine():
    path = settings.db_path
    if not path.startswith("/"):
        path = f"./{path}"
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        echo=False,
        connect_args={"timeout": 15},
    )

    @event.listens_for(eng.sync_engine, "connect")
    def _set_wal(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA synchronous=NORMAL")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return eng


engine = _make_engine()

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
