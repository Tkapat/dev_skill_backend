from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.audit import audit
from app.core.deps import require
from app.core.limiter import limiter
from app.db.mongo import db, oid, ser
from app.services.dbfuncs import generate_sessions

router = APIRouter()
GOVT_WRITE = ("super_admin", "scheme_officer")
GOVT_READ = (*GOVT_WRITE, "auditor")


@router.get("/my/requests")
@limiter.limit("100/minute")
def list_my_requests(
    request: Request,
    kind: str | None = None,
    status: str | None = None,
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require("centre_admin")),
):
    cid = user["centre_id"]
    items = []
    for coll_name in ("change_requests", "repair_declarations"):
        if kind and kind != coll_name.replace("_", ""):
            continue
        q = {"centre_id": cid}
        if status:
            q["status"] = status
        if cursor:
            q["_id"] = {"$lt": oid(cursor)}
        for doc in db[coll_name].find(q).sort("_id", -1).limit(limit + 1):
            doc["kind"] = (
                "enrolment"
                if coll_name == "change_requests" and doc.get("kind") == "enrolment"
                else coll_name.replace("_", "")
            )
            items.append(doc)
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    else:
        next_cursor = None
    return {"items": ser(items), "next_cursor": next_cursor}


@router.post("/my/repair-declarations")
@limiter.limit("30/minute")
def create_repair(request: Request, body: dict, user=Depends(require("centre_admin"))):
    cid = user["centre_id"]
    if (
        body.get("expected_fix_date", "")
        > (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()
    ):
        raise HTTPException(422, detail={"detail": "Max 30 days ahead", "code": "DATE_TOO_FAR"})
    now = datetime.now(timezone.utc)
    doc = {
        "centre_id": cid,
        "equipment_class": body["equipment_class"],
        "qty": body["qty"],
        "reason": body["reason"],
        "expected_fix_date": body["expected_fix_date"],
        "status": "pending",
        "created_at": now,
    }
    doc["_id"] = db.repair_declarations.insert_one(doc).inserted_id
    audit(
        user,
        "repair.create",
        "repair_declaration",
        str(doc["_id"]),
        {"class": doc["equipment_class"]},
    )
    return ser(doc)


@router.post("/my/requests")
@limiter.limit("30/minute")
def create_request(request: Request, body: dict, user=Depends(require("centre_admin"))):
    cid = user["centre_id"]
    kind = body["kind"]
    if kind not in ("equipment", "enrolment", "batch", "camera"):
        raise HTTPException(422, detail={"detail": "Invalid kind", "code": "INVALID_KIND"})
    now = datetime.now(timezone.utc)
    doc = {
        "centre_id": cid,
        "kind": kind,
        "payload": body["payload"],
        "reason": body["reason"],
        "status": "pending",
        "created_at": now,
    }
    doc["_id"] = db.change_requests.insert_one(doc).inserted_id
    audit(user, "request.create", "change_request", str(doc["_id"]), {"kind": kind})
    return ser(doc)


@router.get("/requests")
@limiter.limit("100/minute")
def list_requests(
    request: Request,
    kind: str | None = None,
    status: str | None = None,
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require(*GOVT_READ)),
):
    items = []
    for coll_name in ("change_requests", "repair_declarations"):
        if kind and kind != coll_name.replace("_", ""):
            continue
        q = {}
        if status:
            q["status"] = status
        if cursor:
            q["_id"] = {"$lt": oid(cursor)}
        for doc in db[coll_name].find(q).sort("_id", -1).limit(limit + 1):
            doc["kind"] = (
                "enrolment"
                if coll_name == "change_requests" and doc.get("kind") == "enrolment"
                else coll_name.replace("_", "")
            )
            items.append(doc)
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    else:
        next_cursor = None
    return {"items": ser(items), "next_cursor": next_cursor}


@router.post("/requests/{rid}/decide")
@limiter.limit("30/minute")
def decide_request(request: Request, rid: str, body: dict, user=Depends(require(*GOVT_WRITE))):
    approve = body.get("approve", False)
    comment = body.get("comment", "")
    approved_until = body.get("approved_until")
    now = datetime.now(timezone.utc)

    for coll_name in ("change_requests", "repair_declarations"):
        doc = db[coll_name].find_one({"_id": oid(rid)})
        if doc:
            break
    if not doc:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})

    cid = doc["centre_id"]
    coll = db[coll_name]

    if not approve:
        coll.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "status": "rejected",
                    "decided_by": oid(user["id"]),
                    "decision_comment": comment,
                    "decided_at": now,
                }
            },
        )
        audit(user, "request.reject", coll_name, rid, {"comment": comment})
        return {"status": "rejected"}

    if coll_name == "repair_declarations":
        coll.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "status": "approved",
                    "decided_by": oid(user["id"]),
                    "decision_comment": comment,
                    "decided_at": now,
                    "approved_until": approved_until,
                }
            },
        )
        audit(user, "repair.approve", "repair_declaration", rid, {"approved_until": approved_until})
        return {"status": "approved", "approved_until": approved_until}

    kind = doc.get("kind")
    payload = doc.get("payload", {})

    if kind == "equipment":
        eclass = payload["class"]
        qty = payload.get("qty", 1)
        db.centres.update_one(
            {"_id": cid, "equipment.class": eclass}, {"$inc": {"equipment.$.sanctioned_qty": qty}}
        )
        audit(user, "equipment.approve", "centre", str(cid), {"class": eclass, "qty": qty})
    elif kind == "enrolment":
        extra = payload.get("extra", 1)
        db.centres.update_one({"_id": cid}, {"$inc": {"max_trainees": extra}})
        audit(user, "enrolment.approve", "centre", str(cid), {"extra": extra})
    elif kind == "batch":
        b = db.batches.insert_one(
            {**payload, "centre_id": cid, "active": True, "created_at": now}
        ).inserted_id

        today = datetime.now(timezone.utc).date()
        generate_sessions(cid, today, today + timedelta(days=14))
        audit(user, "batch.approve", "centre", str(cid), {"batch_id": str(b)})
    elif kind == "camera":
        pass

    coll.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": "approved",
                "decided_by": oid(user["id"]),
                "decision_comment": comment,
                "decided_at": now,
            }
        },
    )
    return {"status": "approved"}
