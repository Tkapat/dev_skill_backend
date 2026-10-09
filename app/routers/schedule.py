from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.audit import audit
from app.core.deps import require
from app.core.limiter import limiter
from app.db.mongo import db, oid, ser
from app.services.dbfuncs import generate_sessions

router = APIRouter()


@router.get("/my/batches")
@limiter.limit("100/minute")
def list_batches(
    request: Request,
    active_only: bool = True,
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require("centre_admin", "trainer")),
):
    cid = user["centre_id"]
    q = {"centre_id": cid}
    if active_only:
        q["active"] = True
    if cursor:
        q["_id"] = {"$lt": oid(cursor)}
    items = list(db.batches.find(q).sort("_id", -1).limit(limit + 1))
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    return {"items": ser(items), "next_cursor": next_cursor}


@router.post("/my/batches")
@limiter.limit("30/minute")
def create_batch(request: Request, body: dict, user=Depends(require("centre_admin"))):
    cid = user["centre_id"]
    centre = db.centres.find_one({"_id": cid})
    if not centre or centre["status"] in ("suspended", "terminated"):
        raise HTTPException(403, detail={"detail": "Centre not active", "code": "CENTRE_INACTIVE"})

    now = datetime.now(timezone.utc)
    trainer_id = oid(body["trainer_id"]) if body.get("trainer_id") else None
    if trainer_id and not db.staff.find_one({"_id": trainer_id, "centre_id": cid, "active": True}):
        raise HTTPException(404, detail={"detail": "Trainer not found", "code": "NOT_FOUND"})

    doc = {
        "centre_id": cid,
        "name": body["name"],
        "trainer_id": trainer_id,
        "weekdays": body["weekdays"],
        "start_time": body["start_time"],
        "end_time": body["end_time"],
        "start_date": body.get("start_date"),
        "end_date": body.get("end_date"),
        "active": True,
        "created_at": now,
    }
    doc["_id"] = db.batches.insert_one(doc).inserted_id
    audit(user, "batch.create", "batch", str(doc["_id"]), {"name": doc["name"]})

    if centre["status"] == "live":
        today = datetime.now(timezone.utc).date()
        generate_sessions(cid, today, today)
    return ser(doc)


@router.get("/my/batches/{bid}")
@limiter.limit("100/minute")
def get_batch(request: Request, bid: str, user=Depends(require("centre_admin", "trainer"))):
    b = db.batches.find_one({"_id": oid(bid)})
    if not b or str(b["centre_id"]) != str(user["centre_id"]):
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    return ser(b)


@router.put("/my/batches/{bid}")
@limiter.limit("30/minute")
def update_batch(request: Request, bid: str, body: dict, user=Depends(require("centre_admin"))):
    b = db.batches.find_one({"_id": oid(bid)})
    if not b or str(b["centre_id"]) != str(user["centre_id"]):
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})

    centre = db.centres.find_one({"_id": user["centre_id"]})
    if centre["status"] not in ("pending_setup", "awaiting_camera_approval"):
        raise HTTPException(
            403, detail={"detail": "Use change request for live centres", "code": "NEEDS_APPROVAL"}
        )

    trainer_id = oid(body["trainer_id"]) if body.get("trainer_id") else None
    if trainer_id and not db.staff.find_one(
        {"_id": trainer_id, "centre_id": b["centre_id"], "active": True}
    ):
        raise HTTPException(404, detail={"detail": "Trainer not found", "code": "NOT_FOUND"})

    now = datetime.now(timezone.utc)
    update_fields = {
        k: body[k]
        for k in (
            "name",
            "trainer_id",
            "weekdays",
            "start_time",
            "end_time",
            "start_date",
            "end_date",
            "active",
        )
        if k in body
    }
    update_fields["updated_at"] = now
    db.batches.update_one({"_id": b["_id"]}, {"$set": update_fields})
    audit(user, "batch.update", "batch", bid, body)
    return ser({**b, **update_fields})


@router.get("/my/holidays")
@limiter.limit("100/minute")
def list_holidays(request: Request, user=Depends(require("centre_admin", "trainer"))):
    cid = user["centre_id"]
    return ser(list(db.holidays.find({"centre_id": cid}).sort("holiday_date", -1)))


@router.post("/my/holidays")
@limiter.limit("30/minute")
def create_holiday(request: Request, body: dict, user=Depends(require("centre_admin"))):
    cid = user["centre_id"]
    if db.holidays.find_one({"centre_id": cid, "holiday_date": body["holiday_date"]}):
        raise HTTPException(409, detail={"detail": "Holiday exists", "code": "HOLIDAY_EXISTS"})
    doc = {
        "centre_id": cid,
        "holiday_date": body["holiday_date"],
        "reason": body.get("reason"),
    }
    doc["_id"] = db.holidays.insert_one(doc).inserted_id
    audit(user, "holiday.create", "holiday", str(doc["_id"]), {"date": doc["holiday_date"]})
    return ser(doc)


@router.delete("/my/holidays/{hid}")
@limiter.limit("30/minute")
def delete_holiday(request: Request, hid: str, user=Depends(require("centre_admin"))):
    r = db.holidays.delete_one({"_id": oid(hid), "centre_id": user["centre_id"]})
    if not r.deleted_count:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    audit(user, "holiday.delete", "holiday", hid, {})
    return {"ok": True}


@router.get("/my/sessions/today")
@limiter.limit("100/minute")
def sessions_today(request: Request, user=Depends(require("centre_admin", "trainer"))):
    cid = user["centre_id"]
    today = datetime.now(timezone.utc).date().isoformat()
    return ser(
        list(db.sessions.find({"centre_id": cid, "session_date": today}).sort("start_ts", 1))
    )


@router.get("/my/sessions")
@limiter.limit("100/minute")
def list_sessions(
    request: Request,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require("centre_admin", "trainer")),
):
    cid = user["centre_id"]
    q = {"centre_id": cid}
    if from_date:
        q.setdefault("session_date", {})["$gte"] = from_date
    if to_date:
        q.setdefault("session_date", {})["$lte"] = to_date
    if cursor:
        q["_id"] = {"$lt": oid(cursor)}
    items = list(db.sessions.find(q).sort("session_date", -1).limit(limit + 1))
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    return {"items": ser(items), "next_cursor": next_cursor}
