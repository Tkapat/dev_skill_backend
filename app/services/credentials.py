import secrets
import string
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
from app.db.mongo import db
from app.core.security import hash_pw


def gen_password(n: int = 12) -> str:
    sets = [string.ascii_lowercase, string.ascii_uppercase, string.digits, "@#$%&*"]
    pool = "".join(sets)
    while True:
        p = "".join(secrets.choice(pool) for _ in range(n))
        if all(any(c in s for c in p) for s in sets):
            return p


def create_login(login_id, password, role, centre_id, full_name, phone=None):
    now = datetime.now(timezone.utc)
    try:
        return db.users.insert_one({
            "login_id": login_id.strip().lower(),
            "password_hash": hash_pw(password),
            "role": role,
            "centre_id": centre_id,
            "full_name": full_name,
            "phone": phone,
            "must_change_password": True,
            "temp_password_expires_at": now + timedelta(hours=72),
            "active": True,
            "token_version": 0,
            "failed_attempts": 0,
            "locked_until": None,
            "mfa_enabled": False,
            "created_at": now
        }).inserted_id
    except DuplicateKeyError:
        raise HTTPException(409, detail={"detail": "Login already exists", "code": "LOGIN_EXISTS"})