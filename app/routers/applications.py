from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.audit import audit
from app.core.deps import invalidate, require
from app.core.limiter import limiter
from app.core.security import hash_pw
from app.db.mongo import db, gen_tracking_code, next_centre_code, oid, ser
from app.services.credentials import create_login, gen_password
from app.services.plausibility import plausibility

router = APIRouter()
GOVT_WRITE = ("super_admin", "scheme_officer")
OPEN = ["open", "acknowledged", "centre_responded", "under_review", "escalated"]


def create_centre_from_application(a: dict, user, created_by_govt=False):
    """Shared by approve and POST /centres (govt direct). Returns (centre, login_id, password)."""
    now = datetime.now(timezone.utc)
    code = next_centre_code()
    centre = {
        "code": code,
        "application_id": a["_id"],
        "name": a["centre_name"],
        "scheme_id": a["scheme_id"],
        "trade_template_id": a["trade_template_id"],
        "max_trainees": a["max_trainees"],
        "enrolled_count": 0,
        "address": a["address"],
        "district": a["district"],
        "state": a["state"],
        "lat": a.get("lat"),
        "lng": a.get("lng"),
        "contact_phone": a["contact_phone"],
        "contact_email": a.get("contact_email"),
        "equipment": [
            {
                "class": e["class"],
                "label": e.get("label", e["class"]),
                "sanctioned_qty": e["qty"],
                "declared_qty": 0,
            }
            for e in a["equipment"]
        ],
        "status": "pending_setup",
        "risk_score": 0,
        "created_by_govt": created_by_govt,
        "created_at": now,
        "updated_at": now,
    }
    cid = db.centres.insert_one(centre).inserted_id
    try:
        pwd = gen_password()
        create_login(
            code, pwd, "centre_admin", cid, a["centre_name"] + " Admin", a["contact_phone"]
        )
    except Exception:
        db.centres.delete_one({"_id": cid})
        raise
    return {**centre, "_id": cid}, code, pwd


@router.post("/public/applications")
@limiter.limit("20/minute")
def create_application(request: Request, body: dict):
    now = datetime.now(timezone.utc)
    tpl = db.trade_templates.find_one({"_id": oid(body["trade_template_id"])})
    if not tpl:
        raise HTTPException(404, detail={"detail": "Trade template not found", "code": "NOT_FOUND"})
    warns = plausibility(
        body["max_trainees"], body.get("equipment", []), tpl.get("equipment_rules", [])
    )
    doc = {
        "tracking_code": gen_tracking_code(),
        "applicant_name": body["applicant_name"],
        "applicant_type": body["applicant_type"],
        "contact_phone": body["contact_phone"],
        "contact_email": body.get("contact_email"),
        "centre_name": body["centre_name"],
        "scheme_id": tpl["scheme_id"],
        "trade_template_id": tpl["_id"],
        "trainers": body.get("trainers", []),
        "max_trainees": body["max_trainees"],
        "equipment": body.get("equipment", []),
        "address": body["address"],
        "district": body["district"],
        "state": body["state"],
        "lat": body.get("lat"),
        "lng": body.get("lng"),
        "room_info": body.get("room_info"),
        "internet_availability": body.get("internet_availability"),
        "proposed_batches": body.get("proposed_batches"),
        "documents": [],
        "status": "submitted",
        "review_comment": None,
        "reviewed_by": None,
        "centre_id": None,
        "created_by_govt": False,
        "submitted_at": now,
        "updated_at": now,
        "events": [{"actor": None, "action": "submitted", "comment": None, "ts": now}],
    }
    doc["_id"] = db.applications.insert_one(doc).inserted_id
    return {"tracking_code": doc["tracking_code"], "plausibility_warnings": warns}


@router.get("/public/applications/track")
@limiter.limit("20/minute")
def track_application(request: Request, code: str, phone: str):
    a = db.applications.find_one({"tracking_code": code, "contact_phone": phone})
    if not a:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    return {"status": a["status"], "review_comment": a.get("review_comment")}


@router.put("/public/applications/{code}")
@limiter.limit("20/minute")
def resubmit_application(request: Request, code: str, body: dict):
    a = db.applications.find_one(
        {"tracking_code": code, "contact_phone": body.get("contact_phone")}
    )
    if not a or a["status"] != "query_raised":
        raise HTTPException(404, detail={"detail": "Not resubmittable", "code": "BAD_STATE"})
    now = datetime.now(timezone.utc)
    db.applications.update_one(
        {"_id": a["_id"]},
        {
            "$set": {
                "status": "resubmitted",
                "max_trainees": body.get("max_trainees", a["max_trainees"]),
                "equipment": body.get("equipment", a["equipment"]),
                "updated_at": now,
            },
            "$push": {
                "events": {"actor": None, "action": "resubmitted", "comment": None, "ts": now}
            },
        },
    )
    return {"status": "resubmitted"}


@router.get("/public/schemes")
@limiter.limit("20/minute")
def list_schemes(request: Request):
    return ser(list(db.schemes.find({"active": True}).sort("name", 1)))


@router.post("/applications/{app_id}/approve")
def approve(app_id: str, user=Depends(require(*GOVT_WRITE))):
    now = datetime.now(timezone.utc)
    prev = db.applications.find_one_and_update(
        {"_id": oid(app_id), "status": {"$in": ["submitted", "under_review", "resubmitted"]}},
        {"$set": {"status": "approved", "reviewed_by": oid(user["id"]), "updated_at": now}},
    )
    if not prev:
        raise HTTPException(409, detail={"detail": "Not approvable", "code": "BAD_STATE"})
    try:
        centre, code, pwd = create_centre_from_application(prev, user)
    except Exception:
        db.applications.update_one({"_id": prev["_id"]}, {"$set": {"status": prev["status"]}})
        raise
    db.applications.update_one(
        {"_id": prev["_id"]},
        {
            "$set": {"centre_id": centre["_id"]},
            "$push": {
                "events": {
                    "actor": oid(user["id"]),
                    "action": "approved",
                    "comment": None,
                    "ts": now,
                }
            },
        },
    )
    audit(user, "application.approve", "application", app_id, {"centre_code": code})
    return {
        "centre_id": str(centre["_id"]),
        "login_id": code,
        "temporary_password": pwd,
        "expires_in_hours": 72,
        "note": "Shown once. Share securely.",
    }


@router.post("/applications/{app_id}/reject")
def reject(app_id: str, body: dict, user=Depends(require(*GOVT_WRITE))):
    now = datetime.now(timezone.utc)
    prev = db.applications.find_one_and_update(
        {"_id": oid(app_id), "status": {"$in": ["submitted", "under_review", "resubmitted"]}},
        {
            "$set": {
                "status": "rejected",
                "reviewed_by": oid(user["id"]),
                "review_comment": body.get("comment", ""),
                "updated_at": now,
            }
        },
    )
    if not prev:
        raise HTTPException(409, detail={"detail": "Not rejectable", "code": "BAD_STATE"})
    db.applications.update_one(
        {"_id": prev["_id"]},
        {
            "$push": {
                "events": {
                    "actor": oid(user["id"]),
                    "action": "rejected",
                    "comment": body.get("comment", ""),
                    "ts": now,
                }
            }
        },
    )
    audit(user, "application.reject", "application", app_id, {"comment": body.get("comment")})
    return {"status": "rejected"}


@router.post("/applications/{app_id}/query")
def query(app_id: str, body: dict, user=Depends(require(*GOVT_WRITE))):
    now = datetime.now(timezone.utc)
    prev = db.applications.find_one_and_update(
        {"_id": oid(app_id), "status": {"$in": ["submitted", "under_review", "resubmitted"]}},
        {
            "$set": {
                "status": "query_raised",
                "reviewed_by": oid(user["id"]),
                "review_comment": body.get("comment", ""),
                "updated_at": now,
            }
        },
    )
    if not prev:
        raise HTTPException(409, detail={"detail": "Not queryable", "code": "BAD_STATE"})
    db.applications.update_one(
        {"_id": prev["_id"]},
        {
            "$push": {
                "events": {
                    "actor": oid(user["id"]),
                    "action": "query",
                    "comment": body.get("comment", ""),
                    "ts": now,
                }
            }
        },
    )
    audit(user, "application.query", "application", app_id, {"comment": body.get("comment")})
    return {"status": "query_raised"}


@router.get("/applications")
def list_applications(status: str | None = None, user=Depends(require(*GOVT_WRITE, "auditor"))):
    q = {"status": status} if status else {}
    return ser(list(db.applications.find(q).sort("submitted_at", -1)))


@router.get("/applications/{app_id}")
def get_application(app_id: str, user=Depends(require(*GOVT_WRITE, "auditor"))):
    a = db.applications.find_one({"_id": oid(app_id)})
    if not a:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    tpl = db.trade_templates.find_one({"_id": a["trade_template_id"]})
    warns = (
        plausibility(a["max_trainees"], a.get("equipment", []), tpl.get("equipment_rules", []))
        if tpl
        else []
    )
    return {**ser(a), "plausibility_warnings": warns}


@router.post("/centres")
def create_centre_direct(body: dict, user=Depends(require("super_admin"))):
    """Govt direct create (skips review, audit-logged)."""
    now = datetime.now(timezone.utc)
    tpl = db.trade_templates.find_one({"_id": oid(body["trade_template_id"])})
    if not tpl:
        raise HTTPException(404, detail={"detail": "Trade template not found", "code": "NOT_FOUND"})
    warns = plausibility(
        body["max_trainees"], body.get("equipment", []), tpl.get("equipment_rules", [])
    )
    doc = {
        "tracking_code": gen_tracking_code(),
        "applicant_name": "Government (Direct)",
        "applicant_type": "other",
        "contact_phone": body["contact_phone"],
        "contact_email": body.get("contact_email"),
        "centre_name": body["centre_name"],
        "scheme_id": tpl["scheme_id"],
        "trade_template_id": tpl["_id"],
        "trainers": body.get("trainers", []),
        "max_trainees": body["max_trainees"],
        "equipment": body.get("equipment", []),
        "address": body["address"],
        "district": body["district"],
        "state": body["state"],
        "lat": body.get("lat"),
        "lng": body.get("lng"),
        "room_info": body.get("room_info"),
        "internet_availability": body.get("internet_availability"),
        "proposed_batches": body.get("proposed_batches"),
        "documents": [],
        "status": "approved",
        "review_comment": None,
        "reviewed_by": oid(user["id"]),
        "centre_id": None,
        "created_by_govt": True,
        "submitted_at": now,
        "updated_at": now,
        "events": [
            {"actor": oid(user["id"]), "action": "direct_approved", "comment": None, "ts": now}
        ],
    }
    app_id = db.applications.insert_one(doc).inserted_id
    centre, code, pwd = create_centre_from_application(doc, user, created_by_govt=True)
    db.applications.update_one({"_id": app_id}, {"$set": {"centre_id": centre["_id"]}})
    audit(user, "application.direct_create", "application", str(app_id), {"centre_code": code})
    return {
        "centre_id": str(centre["_id"]),
        "login_id": code,
        "temporary_password": pwd,
        "expires_in_hours": 72,
        "note": "Shown once. Share securely.",
        "plausibility_warnings": warns,
    }


@router.get("/centres")
def list_centres(
    district: str | None = None,
    status: str | None = None,
    q: str | None = None,
    user=Depends(require(*GOVT_WRITE, "auditor")),
):
    query = {}
    if district:
        query["district"] = district
    if status:
        query["status"] = status
    if q:
        query["$or"] = [
            {"code": {"$regex": q, "$options": "i"}},
            {"name": {"$regex": q, "$options": "i"}},
        ]
    centres = list(db.centres.find(query).sort("created_at", -1))
    centre_ids = [c["_id"] for c in centres]
    flags_by_centre = {}
    if centre_ids:
        for f in db.flags.find({"centre_id": {"$in": centre_ids}, "status": {"$in": OPEN}}):
            flags_by_centre.setdefault(str(f["centre_id"]), 0)
            flags_by_centre[str(f["centre_id"])] += 1
    cams_by_centre = {}
    if centre_ids:
        for c in db.cameras.find({"centre_id": {"$in": centre_ids}}):
            cams_by_centre.setdefault(
                str(c["centre_id"]), {"online": 0, "offline": 0, "tampered": 0}
            )
            cams_by_centre[str(c["centre_id"])][c.get("status", "unknown")] += 1
    out = []
    for c in centres:
        cid = str(c["_id"])
        out.append(
            {
                **ser(c),
                "open_flags": flags_by_centre.get(cid, 0),
                "cameras": cams_by_centre.get(cid, {"online": 0, "offline": 0, "tampered": 0}),
            }
        )
    return out


@router.get("/centres/{cid}")
def get_centre(cid: str, user=Depends(require(*GOVT_WRITE, "auditor"))):
    c = db.centres.find_one({"_id": oid(cid)})
    if not c:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    cams = list(db.cameras.find({"centre_id": oid(cid)}))
    flags = list(
        db.flags.find({"centre_id": oid(cid), "status": {"$in": OPEN}}).sort("created_at", -1)
    )
    return {"centre": ser(c), "cameras": ser(cams), "open_flags": ser(flags)}


@router.post("/centres/{cid}/reset-credentials")
def reset_credentials(cid: str, user=Depends(require(*GOVT_WRITE))):
    c = db.centres.find_one({"_id": oid(cid)})
    if not c:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    admin = db.users.find_one({"centre_id": oid(cid), "role": "centre_admin"})
    if not admin:
        raise HTTPException(404, detail={"detail": "Centre admin not found", "code": "NOT_FOUND"})
    pwd = gen_password()
    db.users.update_one(
        {"_id": admin["_id"]},
        {
            "$set": {
                "password_hash": hash_pw(pwd),
                "must_change_password": True,
                "temp_password_expires_at": datetime.now(timezone.utc) + timedelta(hours=72),
            },
            "$inc": {"token_version": 1},
        },
    )
    db.refresh_tokens.delete_many({"user_id": admin["_id"]})
    invalidate(admin["_id"])
    return {
        "login_id": c["code"],
        "temporary_password": pwd,
        "expires_in_hours": 72,
        "note": "Shown once. Share securely.",
    }
