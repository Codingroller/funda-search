from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Request


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


async def require_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise UnauthenticatedException()
