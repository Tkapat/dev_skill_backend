from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.audit import audit
from app.core.deps import require
from app.core.limiter import limiter
from app.db.mongo import db, oid, ser

router = APIRouter()
GOVT_WRITE = ("super_admin", "scheme_officer")
GOVT_READ = (*GOVT_WRITE, "auditor")
CENTRE_ROLES = ("centre_admin", "trainer")
LEVELS = ("advisory", "notice", "warning", "suspension", "termination")


@router.get("/notices")
@limiter.limit("100/minute")
def list_notices(
    request: Request,
    centre_id: str | None = None,
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require(*GOVT_READ)),
):
    q = {}
    if centre_id:
        q["centre_id"] = oid(centre_id)
    if cursor:
        q["_id"] = {"$lt": oid(cursor)}
    items = list(db.notices.find(q).sort("_id", -1).limit(limit + 1))
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    return {"items": ser(items), "next_cursor": next_cursor}


@router.post("/notices")
@limiter.limit("30/minute")
def create_notice(request: Request, body: dict, user=Depends(require(*GOVT_WRITE))):
    level = body.get("level")
    if level not in LEVELS:
        raise HTTPException(422, detail={"detail": "Invalid level", "code": "INVALID_LEVEL"})
    if level in ("suspension", "termination") and not body.get("confirmation"):
        raise HTTPException(
            422, detail={"detail": "Confirmation required", "code": "NO_CONFIRMATION"}
        )

    cid = body["centre_id"]
    centre = db.centres.find_one({"_id": oid(cid)})
    if not centre:
        raise HTTPException(404, detail={"detail": "Centre not found", "code": "NOT_FOUND"})

    now = datetime.now(timezone.utc)
    doc = {
        "centre_id": oid(cid),
        "level": level,
        "subject": body["subject"],
        "body": body["body"],
        "flag_ids": [oid(f) for f in body.get("flag_ids", [])],
        "issued_by": oid(user["id"]),
        "created_at": now,
    }
    doc["_id"] = db.notices.insert_one(doc).inserted_id

    if level == "suspension":
        db.centres.update_one(
            {"_id": oid(cid)}, {"$set": {"status": "suspended", "status_reason": doc["subject"]}}
        )
    elif level == "termination":
        db.centres.update_one(
            {"_id": oid(cid)}, {"$set": {"status": "terminated", "status_reason": doc["subject"]}}
        )

    audit(user, f"notice.{level}", "notice", str(doc["_id"]), {"centre_id": cid, "level": level})
    return ser(doc)


@router.get("/my/notices")
@limiter.limit("100/minute")
def list_my_notices(
    request: Request,
    unread_only: bool = False,
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require(*CENTRE_ROLES)),
):
    cid = user["centre_id"]
    q = {"centre_id": cid}
    if unread_only:
        q["read_at"] = None
    if cursor:
        q["_id"] = {"$lt": oid(cursor)}
    items = list(db.notices.find(q).sort("_id", -1).limit(limit + 1))
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    return {"items": ser(items), "next_cursor": next_cursor}


@router.post("/my/notices/{nid}/read")
@limiter.limit("30/minute")
def mark_notice_read(request: Request, nid: str, user=Depends(require(*CENTRE_ROLES))):
    n = db.notices.find_one({"_id": oid(nid), "centre_id": user["centre_id"]})
    if not n:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    db.notices.update_one({"_id": n["_id"]}, {"$set": {"read_at": datetime.now(timezone.utc)}})
    return {"ok": True}


@router.post("/my/notices/{nid}/reply")
@limiter.limit("30/minute")
def reply_notice(request: Request, nid: str, body: dict, user=Depends(require(*CENTRE_ROLES))):
    n = db.notices.find_one({"_id": oid(nid), "centre_id": user["centre_id"]})
    if not n:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    now = datetime.now(timezone.utc)
    db.notices.update_one(
        {"_id": n["_id"]}, {"$set": {"reply": body.get("reply", ""), "replied_at": now}}
    )
    return {"ok": True}
