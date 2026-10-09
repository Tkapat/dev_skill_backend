from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.audit import audit
from app.core.deps import require
from app.core.limiter import limiter
from app.db.mongo import db, oid, ser
from app.services.dbfuncs import recompute_risk

router = APIRouter()
GOVT_WRITE = ("super_admin", "scheme_officer")
GOVT_READ = (*GOVT_WRITE, "auditor")
OPEN = ["open", "acknowledged", "centre_responded", "under_review", "escalated"]
CENTRE_ROLES = ("centre_admin", "trainer")
ACTIONS = {
    "acknowledge": "acknowledged",
    "resolve": "resolved",
    "dismiss": "dismissed",
    "escalate": "escalated",
}


@router.get("/flags")
@limiter.limit("100/minute")
def list_flags(
    request: Request,
    severity: str | None = None,
    type: str | None = None,
    status: str | None = None,
    state: str | None = None,
    centre_id: str | None = None,
    district: str | None = None,
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require(*GOVT_READ)),
):
    q = {}
    if severity:
        q["severity"] = severity
    if type:
        q["type"] = type
    if status:
        q["status"] = status
    if state:
        q["state"] = state
    if centre_id:
        q["centre_id"] = oid(centre_id)
    if district:
        q["district"] = district
    if cursor:
        q["_id"] = {"$lt": oid(cursor)}

    items = list(db.flags.find(q).sort("_id", -1).limit(limit + 1))
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    return {"items": ser(items), "next_cursor": next_cursor}


@router.get("/flags/{fid}")
@limiter.limit("100/minute")
def get_flag(request: Request, fid: str, user=Depends(require(*GOVT_READ, *CENTRE_ROLES))):
    f = db.flags.find_one({"_id": oid(fid)})
    if not f:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    if user["role"] in CENTRE_ROLES and str(f.get("centre_id")) != str(user.get("centre_id")):
        raise HTTPException(403, detail={"detail": "Not your centre", "code": "WRONG_CENTRE"})
    return ser(f)


@router.post("/flags/{fid}/action")
@limiter.limit("30/minute")
def flag_action(request: Request, fid: str, body: dict, user=Depends(require(*GOVT_WRITE))):
    f = db.flags.find_one({"_id": oid(fid)})
    if not f:
        raise HTTPException(444, detail={"detail": "Not found", "code": "NOT_FOUND"})
    action = body.get("action")
    if action not in ACTIONS:
        raise HTTPException(422, detail={"detail": "Invalid action", "code": "INVALID_ACTION"})
    if action in ("dismiss", "resolve") and not body.get("comment"):
        raise HTTPException(422, detail={"detail": "Comment required", "code": "NO_COMMENT"})

    new_status = ACTIONS[action]
    now = datetime.now(timezone.utc)
    event = {
        "actor": oid(user["id"]),
        "actor_role": user["role"],
        "action": action,
        "comment": body.get("comment"),
        "ts": now,
    }
    db.flags.update_one(
        {"_id": f["_id"]},
        {"$set": {"status": new_status, "updated_at": now}, "$push": {"events": event}},
    )
    if new_status in ("resolved", "dismissed"):
        db.flags.update_one({"_id": f["_id"]}, {"$set": {"resolved_at": now}})
    recompute_risk(f["centre_id"])
    audit(user, f"flag.{action}", "flag", fid, {"new_status": new_status})
    return {"status": new_status}


@router.post("/flags/bulk-acknowledge")
@limiter.limit("30/minute")
def bulk_acknowledge(request: Request, body: dict, user=Depends(require(*GOVT_WRITE))):
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(422, detail={"detail": "ids[] required", "code": "NO_IDS"})
    now = datetime.now(timezone.utc)
    count = 0
    for fid in ids:
        f = db.flags.find_one({"_id": oid(fid)})
        if f and f["status"] in ("open", "centre_responded"):
            db.flags.update_one(
                {"_id": f["_id"]},
                {
                    "$set": {"status": "acknowledged", "updated_at": now},
                    "$push": {
                        "events": {
                            "actor": oid(user["id"]),
                            "actor_role": user["role"],
                            "action": "acknowledge",
                            "ts": now,
                        }
                    },
                },
            )
            count += 1
    return {"acknowledged": count}


@router.get("/my/flags")
@limiter.limit("100/minute")
def list_my_flags(
    request: Request,
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require(*CENTRE_ROLES)),
):
    cid = user["centre_id"]
    q = {"centre_id": cid}
    if cursor:
        q["_id"] = {"$lt": oid(cursor)}
    items = list(db.flags.find(q).sort("_id", -1).limit(limit + 1))
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    return {"items": ser(items), "next_cursor": next_cursor}


@router.post("/my/flags/{fid}/respond")
@limiter.limit("30/minute")
def respond_flag(request: Request, fid: str, body: dict, user=Depends(require(*CENTRE_ROLES))):
    f = db.flags.find_one({"_id": oid(fid)})
    if not f or str(f["centre_id"]) != str(user["centre_id"]):
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    now = datetime.now(timezone.utc)
    event = {
        "actor": oid(user["id"]),
        "actor_role": user["role"],
        "action": "centre_respond",
        "comment": body.get("comment"),
        "attachments": body.get("attachments"),
        "ts": now,
    }
    db.flags.update_one(
        {"_id": f["_id"]},
        {"$set": {"status": "centre_responded", "updated_at": now}, "$push": {"events": event}},
    )
    audit(user, "flag.respond", "flag", fid, {"comment": body.get("comment")})
    return {"status": "centre_responded"}
