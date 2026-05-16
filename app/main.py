import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from app.auth import UnauthenticatedException, hash_password
from app.config import settings
from app.db import AsyncSessionLocal, engine, Base
from app.models import User
from app.scheduler import scheduler, reconcile_jobs


_WEAK_KEYS = {"change-me", "secret", "changeme", "password", "test", ""}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.secret_key in _WEAK_KEYS or len(settings.secret_key) < 32:
        raise RuntimeError(
            "SECRET_KEY is not set to a secure value. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        user = result.scalar_one_or_none()
        if user is None and settings.admin_password:
            db.add(
                User(
                    password_hash=hash_password(settings.admin_password),
                    ntfy_topic=secrets.token_urlsafe(16),
                )
            )
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


# Routers
from app.routes import auth as auth_routes  # noqa: E402
from app.routes import queries as queries_routes  # noqa: E402
from app.routes import settings as settings_routes  # noqa: E402

app.include_router(auth_routes.router)
app.include_router(queries_routes.router)
app.include_router(settings_routes.router)
