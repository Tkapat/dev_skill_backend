from datetime import datetime, timedelta, timezone

import pyotp
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import settings
from app.core.deps import GOVT_ROLES, invalidate, require
from app.core.limiter import limiter
from app.core.security import (
    REFRESH_DAYS,
    check_pw,
    decode,
    hash_pw,
    hash_refresh,
    make_access,
    make_mfa_token,
    new_refresh,
    password_ok,
)
from app.db.mongo import db, oid, ser

router = APIRouter()
_f = Fernet(settings.fernet_key.encode())
MAX_FAILS, LOCK_MIN = 5, 15


def _bad(status, code, msg):
    raise HTTPException(status, detail={"detail": msg, "code": code})


def _now():
    return datetime.now(timezone.utc)


def _public(u):
    return ser(
        {
            k: u.get(k)
            for k in (
                "_id",
                "login_id",
                "role",
                "centre_id",
                "full_name",
                "must_change_password",
                "mfa_enabled",
            )
        }
    )


def _issue(u) -> dict:
    raw, h = new_refresh()
    db.refresh_tokens.insert_one(
        {
            "user_id": u["_id"],
            "token_hash": h,
            "expires_at": _now() + timedelta(days=REFRESH_DAYS),
            "created_at": _now(),
        }
    )
    return {"access_token": make_access(u), "refresh_token": raw, "user": _public(u)}


@router.post("/auth/login")
@limiter.limit("10/minute")
def login(request: Request, body: dict):
    login_id = str(body.get("login_id", "")).strip().lower()
    u = db.users.find_one({"login_id": login_id})
    if u and u.get("locked_until") and u["locked_until"] > _now():
        _bad(429, "ACCOUNT_LOCKED", "Too many attempts. Try again in a few minutes.")
    if not (u and u["active"] and check_pw(u["password_hash"], str(body.get("password", "")))):
        if u:
            fails = u.get("failed_attempts", 0) + 1
            upd = {"failed_attempts": fails}
            if fails >= MAX_FAILS:
                upd = {"failed_attempts": 0, "locked_until": _now() + timedelta(minutes=LOCK_MIN)}
            db.users.update_one({"_id": u["_id"]}, {"$set": upd})
        _bad(401, "INVALID_CREDENTIALS", "Incorrect ID or password")
    db.users.update_one(
        {"_id": u["_id"]},
        {"$set": {"failed_attempts": 0, "locked_until": None, "last_login_at": _now()}},
    )
    if u.get("mfa_enabled") and u["role"] in GOVT_ROLES:
        return {"mfa_required": True, "mfa_token": make_mfa_token(u)}
    return _issue(u)


@router.post("/auth/mfa/login")
def mfa_login(body: dict):
    try:
        claims = decode(body["mfa_token"], "mfa")
    except Exception:
        _bad(401, "BAD_TOKEN", "MFA step expired. Log in again.")
    u = db.users.find_one({"_id": oid(claims["sub"])})
    secret = _f.decrypt(u["mfa_secret_enc"]).decode()
    if not pyotp.TOTP(secret).verify(str(body.get("code", "")), valid_window=1):
        _bad(401, "BAD_MFA", "Incorrect code")
    return _issue(u)


@router.post("/auth/refresh")
def refresh(body: dict):
    h = hash_refresh(str(body.get("refresh_token", "")))
    t = db.refresh_tokens.find_one_and_delete({"token_hash": h})
    if not t or t["expires_at"] < _now():
        _bad(401, "BAD_TOKEN", "Please log in again")
    u = db.users.find_one({"_id": t["user_id"]})
    if not u or not u["active"]:
        _bad(401, "BAD_TOKEN", "Please log in again")
    return _issue(u)


@router.post("/auth/logout")
def logout(body: dict):
    db.refresh_tokens.delete_one({"token_hash": hash_refresh(str(body.get("refresh_token", "")))})
    return {"ok": True}


@router.get("/me")
def me(
    user=Depends(
        require(
            *["super_admin", "scheme_officer", "auditor", "centre_admin", "trainer"],
            allow_unchanged_password=True,
        )
    ),
):
    centre = (
        db.centres.find_one({"_id": user["centre_id"]}, {"code": 1, "name": 1, "status": 1})
        if user.get("centre_id")
        else None
    )
    return {**_public(user), "centre": ser(centre)}


@router.post("/me/change-password")
def change_password(
    body: dict,
    user=Depends(
        require(
            *["super_admin", "scheme_officer", "auditor", "centre_admin", "trainer"],
            allow_unchanged_password=True,
        )
    ),
):
    full = db.users.find_one({"_id": user["_id"]})
    if not check_pw(full["password_hash"], str(body.get("current_password", ""))):
        _bad(401, "INVALID_CREDENTIALS", "Current password is incorrect")
    new = str(body.get("new_password", ""))
    if not password_ok(new):
        _bad(422, "WEAK_PASSWORD", "Use 10+ characters with upper, lower, digit and special.")
    if check_pw(full["password_hash"], new):
        _bad(422, "SAME_PASSWORD", "Choose a different password")
    db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": hash_pw(new),
                "must_change_password": False,
                "temp_password_expires_at": None,
            },
            "$inc": {"token_version": 1},
        },
    )
    db.refresh_tokens.delete_many({"user_id": user["_id"]})
    invalidate(user["id"])
    return _issue(db.users.find_one({"_id": user["_id"]}))


@router.post("/me/mfa/setup")
def mfa_setup(user=Depends(require("super_admin", "scheme_officer", "auditor"))):
    secret = pyotp.random_base32()
    db.users.update_one(
        {"_id": user["_id"]}, {"$set": {"mfa_pending_enc": _f.encrypt(secret.encode())}}
    )
    return {
        "otpauth_uri": pyotp.TOTP(secret).provisioning_uri(
            name=user["login_id"], issuer_name="Dev_Skill Gov"
        ),
        "secret": secret,
    }


@router.post("/me/mfa/enable")
def mfa_enable(body: dict, user=Depends(require("super_admin", "scheme_officer", "auditor"))):
    full = db.users.find_one({"_id": user["_id"]})
    secret = _f.decrypt(full["mfa_pending_enc"]).decode()
    if not pyotp.TOTP(secret).verify(str(body.get("code", "")), valid_window=1):
        _bad(401, "BAD_MFA", "Incorrect code")
    db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {"mfa_enabled": True, "mfa_secret_enc": full["mfa_pending_enc"]},
            "$unset": {"mfa_pending_enc": ""},
        },
    )
    invalidate(user["id"])
    return {"mfa_enabled": True}
