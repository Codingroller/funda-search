import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from starlette.middleware.sessions import SessionMiddleware

from app.auth import UnauthenticatedException, hash_password
from app.config import settings
from app.db import AsyncSessionLocal, Base, engine
from app.models import User
from app.scheduler import reconcile_jobs, scheduler


_WEAK_KEYS = {"change-me", "secret", "changeme", "password", "test", ""}


async def _run_migrations(conn) -> None:
    """Idempotent schema migrations for existing single-user DBs."""
    for stmt in [
        "ALTER TABLE users ADD COLUMN username TEXT",
        "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE saved_queries ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
    ]:
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass  # column already exists

    # Backfill existing single-user data
    await conn.execute(text(
        "UPDATE users SET username = :u WHERE username IS NULL"
    ), {"u": settings.admin_username})
    await conn.execute(text(
        "UPDATE saved_queries SET user_id = (SELECT id FROM users LIMIT 1) WHERE user_id IS NULL"
    ))
    # No explicit commit — engine.begin() context manager commits on exit


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.secret_key in _WEAK_KEYS or len(settings.secret_key) < 32:
        raise RuntimeError(
            "SECRET_KEY is not set to a secure value. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )

    async with engine.begin() as conn:
        await _run_migrations(conn)
        await conn.run_sync(Base.metadata.create_all)

    # Seed first admin from env vars (new installs only)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).limit(1))
        if result.scalar_one_or_none() is None and settings.admin_password:
            db.add(User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                ntfy_topic=secrets.token_urlsafe(16),
                is_admin=True,
            ))
            await db.commit()

    scheduler.start()
    await reconcile_jobs()

    yield

    scheduler.shutdown()


app = FastAPI(title="Funda Search", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, https_only=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(UnauthenticatedException)
async def unauth_handler(request: Request, _exc: UnauthenticatedException):
    return RedirectResponse("/login", status_code=302)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/img/{filename}")
async def serve_image(filename: str):
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(400)
    path = Path(settings.db_path).parent / "images" / filename
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(str(path), media_type="image/jpeg")


# Routers
from app.routes import auth as auth_routes        # noqa: E402
from app.routes import queries as queries_routes  # noqa: E402
from app.routes import settings as settings_routes  # noqa: E402
from app.routes import admin as admin_routes      # noqa: E402
from app.routes import signup as signup_routes    # noqa: E402

app.include_router(auth_routes.router)
app.include_router(queries_routes.router)
app.include_router(settings_routes.router)
app.include_router(admin_routes.router)
app.include_router(signup_routes.router)
