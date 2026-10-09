from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import require
from app.db.mongo import db, oid, ser

router = APIRouter()


def _cap_err():
    raise HTTPException(
        409,
        detail={
            "detail": "Sanctioned limit reached. Request extra enrolment.",
            "code": "ENROLMENT_CAP_REACHED",
        },
    )


def take_seat(centre_id) -> bool:
    """Atomic: increments only if enrolled_count < max_trainees. Same pattern for reactivation."""
    r = db.centres.update_one(
        {
            "_id": centre_id,
            "status": {"$nin": ["suspended", "terminated"]},
            "$expr": {"$lt": ["$enrolled_count", "$max_trainees"]},
        },
        {"$inc": {"enrolled_count": 1}},
    )
    return r.modified_count == 1


@router.post("/my/trainees")
def enrol(body: dict, user=Depends(require("centre_admin"))):
    cid = user["centre_id"]
    if not take_seat(cid):
        c = db.centres.find_one({"_id": cid}, {"status": 1})
        if c["status"] in ("suspended", "terminated"):
            raise HTTPException(
                403, detail={"detail": "Centre not active", "code": "CENTRE_INACTIVE"}
            )
        _cap_err()
    try:
        doc = {
            "centre_id": cid,
            "batch_id": oid(body["batch_id"]) if body.get("batch_id") else None,
            "full_name": body["full_name"].strip(),
            "external_id": body.get("external_id"),
            "active": True,
            "created_at": datetime.now(timezone.utc),
        }
        doc["_id"] = db.trainees.insert_one(doc).inserted_id
    except Exception:
        db.centres.update_one({"_id": cid}, {"$inc": {"enrolled_count": -1}})
        raise
    return ser(doc)


@router.get("/my/trainees")
def list_trainees(batch_id: str | None = None, user=Depends(require("centre_admin", "trainer"))):
    cid = user["centre_id"]
    q = {"centre_id": cid, "active": True}
    if batch_id:
        q["batch_id"] = oid(batch_id)
    return ser(list(db.trainees.find(q)))


@router.post("/my/trainees/{tid}/deactivate")
def deactivate(tid: str, user=Depends(require("centre_admin"))):
    r = db.trainees.update_one(
        {"_id": oid(tid), "centre_id": user["centre_id"], "active": True},
        {"$set": {"active": False}},
    )
    if r.modified_count:
        db.centres.update_one({"_id": user["centre_id"]}, {"$inc": {"enrolled_count": -1}})
    return {"ok": True}
