from datetime import datetime, timezone

import jwt
from cachetools import TTLCache
from fastapi import Depends, Header, HTTPException

from app.core.security import decode
from app.db.mongo import db, oid

_cache = TTLCache(maxsize=512, ttl=30)
CENTRE_ROLES = {"centre_admin", "trainer"}
GOVT_ROLES = {"super_admin", "scheme_officer", "auditor"}


def invalidate(uid=None):
    _cache.pop(str(uid), None) if uid else _cache.clear()


def _err(status, code, msg):
    raise HTTPException(status, detail={"detail": msg, "code": code})


def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        _err(401, "NO_TOKEN", "Missing token")
    try:
        claims = decode(authorization[7:], "access")
    except jwt.ExpiredSignatureError:
        _err(401, "TOKEN_EXPIRED", "Session expired")
    except Exception:
        _err(401, "BAD_TOKEN", "Invalid token")
    uid = claims["sub"]
    u = _cache.get(uid)
    if u is None:
        u = db.users.find_one({"_id": oid(uid)}, {"password_hash": 0, "mfa_secret_enc": 0})
        if u:
            _cache[uid] = u
    if not u or not u.get("active"):
        _err(403, "DISABLED", "Account disabled")
    if u.get("token_version", 0) != claims.get("tv"):
        _err(401, "BAD_TOKEN", "Session revoked")
    return {**u, "id": str(u["_id"])}


def require(*roles, allow_unchanged_password=False):
    def dep(user=Depends(current_user)):
        if user["role"] not in roles:
            _err(403, "FORBIDDEN", "Forbidden")
        if user.get("must_change_password") and not allow_unchanged_password:
            exp = user.get("temp_password_expires_at")
            if exp and exp < datetime.now(timezone.utc):
                _err(403, "CREDENTIALS_EXPIRED", "Credentials expired")
            _err(403, "PASSWORD_CHANGE_REQUIRED", "Change password first")
        return user

    return dep


def assert_centre_access(user, centre_id):
    if user["role"] in CENTRE_ROLES and str(user.get("centre_id")) != str(centre_id):
        _err(403, "WRONG_CENTRE", "Not your centre")
