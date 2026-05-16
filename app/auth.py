from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request

from app.db import AsyncSessionLocal

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, Exception):
        return False


class UnauthenticatedException(Exception):
    pass


async def get_current_user(request: Request):
    """Return the User for the current session, or None."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    from app.models import User
    async with AsyncSessionLocal() as db:
        return await db.get(User, user_id)


async def require_auth(request: Request):
    """Dependency — returns the current User or raises 302 to /login."""
    user = await get_current_user(request)
    if not user:
        raise UnauthenticatedException()
    return user


async def require_admin(request: Request):
    """Dependency — returns the current User only if is_admin, else 403."""
    user = await require_auth(request)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
