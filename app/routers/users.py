from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.audit import audit
from app.core.deps import require
from app.core.limiter import limiter
from app.core.security import hash_pw, password_ok
from app.db.mongo import db, oid, ser

router = APIRouter()


@router.get("/users")
@limiter.limit("100/minute")
def list_users(
    request: Request,
    role: str | None = None,
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require("super_admin")),
):
    q = {}
    if role:
        q["role"] = role
    if cursor:
        q["_id"] = {"$lt": oid(cursor)}
    items = list(
        db.users.find(q, {"password_hash": 0, "mfa_secret_enc": 0, "mfa_pending_enc": 0})
        .sort("_id", -1)
        .limit(limit + 1)
    )
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    return {"items": ser(items), "next_cursor": next_cursor}


@router.post("/users")
@limiter.limit("30/minute")
def create_user(request: Request, body: dict, user=Depends(require("super_admin"))):
    role = body["role"]
    if role not in ("super_admin", "scheme_officer", "auditor"):
        raise HTTPException(422, detail={"detail": "Invalid role", "code": "INVALID_ROLE"})
    if not password_ok(body.get("password", "")):
        raise HTTPException(422, detail={"detail": "Weak password", "code": "WEAK_PASSWORD"})
    now = datetime.now(timezone.utc)
    login_id = body["login_id"].strip().lower()
    if db.users.find_one({"login_id": login_id}):
        raise HTTPException(409, detail={"detail": "Login exists", "code": "LOGIN_EXISTS"})
    pwd = body["password"]
    doc = {
        "login_id": login_id,
        "password_hash": hash_pw(pwd),
        "role": role,
        "centre_id": None,
        "full_name": body["full_name"],
        "phone": body.get("phone"),
        "must_change_password": body.get("must_change_password", True),
        "temp_password_expires_at": None,
        "active": True,
        "token_version": 0,
        "failed_attempts": 0,
        "locked_until": None,
        "mfa_enabled": False,
        "created_at": now,
    }
    doc["_id"] = db.users.insert_one(doc).inserted_id
    audit(user, "user.create", "user", str(doc["_id"]), {"login_id": login_id, "role": role})
    return ser({**doc, "temporary_password": pwd})


@router.get("/users/{uid}")
@limiter.limit("100/minute")
def get_user(request: Request, uid: str, user=Depends(require("super_admin"))):
    u = db.users.find_one(
        {"_id": oid(uid)}, {"password_hash": 0, "mfa_secret_enc": 0, "mfa_pending_enc": 0}
    )
    if not u:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    return ser(u)


@router.post("/users/{uid}/deactivate")
@limiter.limit("30/minute")
def deactivate_user(request: Request, uid: str, user=Depends(require("super_admin"))):
    u = db.users.find_one({"_id": oid(uid)})
    if not u:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    if u["_id"] == user["_id"]:
        raise HTTPException(
            409, detail={"detail": "Cannot deactivate self", "code": "SELF_DEACTIVATE"}
        )
    db.users.update_one({"_id": u["_id"]}, {"$set": {"active": False}})
    audit(user, "user.deactivate", "user", uid, {})
    return {"ok": True}
