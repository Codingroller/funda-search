from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from starlette.middleware.sessions import SessionMiddleware

from app.auth import UnauthenticatedException, hash_password
from app.config import settings
from app.db import AsyncSessionLocal, Base, engine
from app.models import User
from app.scheduler import cleanup_old_data, reconcile_jobs, scheduler


_WEAK_KEYS = {"change-me", "secret", "changeme", "password", "test", ""}


async def _run_migrations(conn) -> None:
    """Idempotent schema migrations for existing single-user DBs."""
    for stmt in [
        "ALTER TABLE users ADD COLUMN username TEXT",
        "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE saved_queries ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
        "ALTER TABLE liked_listings ADD COLUMN agent_contacted INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE liked_listings ADD COLUMN viewing_date TEXT",
        "ALTER TABLE liked_listings ADD COLUMN bid_amount INTEGER",
        (
            "CREATE TABLE IF NOT EXISTS push_subscriptions ("
            "id INTEGER PRIMARY KEY,"
            "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
            "endpoint TEXT NOT NULL UNIQUE,"
            "p256dh TEXT NOT NULL,"
            "auth TEXT NOT NULL,"
            "user_agent TEXT,"
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ),
        "CREATE INDEX IF NOT EXISTS ix_push_subscriptions_user_id ON push_subscriptions(user_id)",
        "ALTER TABLE run_logs ADD COLUMN all_listings_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE push_subscriptions ADD COLUMN last_used_at DATETIME",
        # Indexes added in architectural review 2026-05-19
        "CREATE INDEX IF NOT EXISTS ix_saved_queries_user_id ON saved_queries(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_run_logs_query_id ON run_logs(query_id)",
        "CREATE INDEX IF NOT EXISTS ix_run_logs_query_started ON run_logs(query_id, started_at)",
        "CREATE INDEX IF NOT EXISTS ix_sharing_from_user ON sharing_connections(from_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_sharing_to_user ON sharing_connections(to_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_invite_expires ON invite_tokens(expires_at)",
    ]:
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass  # column already exists

    # Purge run_logs that predate their query's creation (orphans from missing
    # FK cascade when foreign_keys PRAGMA was off on older installs).
    try:
        await conn.execute(text(
            "DELETE FROM run_logs WHERE started_at < "
            "(SELECT created_at FROM saved_queries WHERE id = run_logs.query_id)"
        ))
    except Exception:
        pass

    # Backfill existing single-user data (silently ignored on fresh installs)
    for stmt, params in [
        ("UPDATE users SET username = :u WHERE username IS NULL", {"u": settings.admin_username}),
        ("UPDATE saved_queries SET user_id = (SELECT id FROM users LIMIT 1) WHERE user_id IS NULL", {}),
    ]:
        try:
            await conn.execute(text(stmt), params)
        except Exception:
            pass
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
                is_admin=True,
            ))
            await db.commit()

    scheduler.start()
    await reconcile_jobs()
    scheduler.add_job(cleanup_old_data, "interval", hours=24, id="cleanup_old_data",
                      replace_existing=True)

    yield

    scheduler.shutdown()


app = FastAPI(title="Funda Search", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, https_only=settings.https_only)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(UnauthenticatedException)
async def unauth_handler(request: Request, _exc: UnauthenticatedException):
    return RedirectResponse("/login", status_code=302)


def _error_response(request: Request, status_code: int, title: str, detail: str):
    from app.templates_env import templates
    try:
        current_user = request.session.get("user_id")
    except Exception:
        current_user = None
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": status_code, "title": title, "detail": detail,
         "current_user": current_user},
        status_code=status_code,
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, _exc):
    return _error_response(request, 404, "Page not found",
                           "The page you're looking for doesn't exist.")


@app.exception_handler(500)
async def server_error_handler(request: Request, _exc):
    return _error_response(request, 500, "Something went wrong",
                           "An unexpected error occurred. Please try again.")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(
        "static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/img/{filename}")
async def serve_image(filename: str):
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(400)
    path = Path(settings.db_path).parent / "images" / filename
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(
        str(path),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=2592000, immutable"},
    )


# Routers
from app.routes import auth as auth_routes        # noqa: E402
from app.routes import queries as queries_routes  # noqa: E402
from app.routes import settings as settings_routes  # noqa: E402
from app.routes import admin as admin_routes      # noqa: E402
from app.routes import signup as signup_routes    # noqa: E402
from app.routes import liked as liked_routes      # noqa: E402
from app.routes import push as push_routes        # noqa: E402

app.include_router(auth_routes.router)
app.include_router(queries_routes.router)
app.include_router(settings_routes.router)
app.include_router(admin_routes.router)
app.include_router(signup_routes.router)
app.include_router(liked_routes.router)
app.include_router(push_routes.router)
