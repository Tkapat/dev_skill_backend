import re
import secrets
import hashlib
import jwt
from datetime import datetime, timedelta, timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from app.core.config import settings

_ph = PasswordHasher()
ACCESS_MIN, REFRESH_DAYS, MFA_MIN = 30, 7, 5
_DUMMY = _ph.hash("dummy-password-for-timing")


def hash_pw(p: str) -> str:
    return _ph.hash(p)


def check_pw(h: str | None, p: str) -> bool:
    try:
        return _ph.verify(h or _DUMMY, p) and h is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_ok(p: str) -> bool:
    return (len(p) >= 10 and re.search(r"[a-z]", p) and re.search(r"[A-Z]", p)
            and re.search(r"\d", p) and re.search(r"[^\w\s]", p)) is not None


def _tok(payload: dict, minutes: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({**payload, "iat": now, "exp": now + timedelta(minutes=minutes)},
                      settings.jwt_secret, algorithm="HS256")


def make_access(u: dict) -> str:
    return _tok({"sub": str(u["_id"]), "tv": u.get("token_version", 0), "typ": "access"}, ACCESS_MIN)


def make_mfa_token(u: dict) -> str:
    return _tok({"sub": str(u["_id"]), "typ": "mfa"}, MFA_MIN)


def decode(token: str, typ: str) -> dict:
    d = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if d.get("typ") != typ:
        raise jwt.InvalidTokenError("wrong type")
    return d


def new_refresh() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()