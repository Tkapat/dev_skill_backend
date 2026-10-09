from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.audit import audit
from app.core.deps import require
from app.core.limiter import limiter
from app.db.mongo import db, oid, ser

router = APIRouter()
GOVT_WRITE = ("super_admin", "scheme_officer")
GOVT_READ = (*GOVT_WRITE, "auditor")


@router.get("/schemes")
@limiter.limit("100/minute")
def list_schemes(request: Request, active_only: bool = True):
    q = {"active": True} if active_only else {}
    return ser(list(db.schemes.find(q).sort("name", 1)))


@router.post("/schemes")
@limiter.limit("30/minute")
def create_scheme(request: Request, body: dict, user=Depends(require("super_admin"))):
    now = datetime.now(timezone.utc)
    if db.schemes.find_one({"code": body["code"]}):
        raise HTTPException(409, detail={"detail": "Scheme code exists", "code": "CODE_EXISTS"})
    doc = {
        "name": body["name"],
        "code": body["code"],
        "description": body.get("description"),
        "active": body.get("active", True),
        "created_at": now,
        "updated_at": now,
    }
    doc["_id"] = db.schemes.insert_one(doc).inserted_id
    audit(user, "scheme.create", "scheme", str(doc["_id"]), {"code": doc["code"]})
    return ser(doc)


@router.get("/schemes/{sid}")
@limiter.limit("100/minute")
def get_scheme(request: Request, sid: str, user=Depends(require(*GOVT_READ))):
    s = db.schemes.find_one({"_id": oid(sid)})
    if not s:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    return ser(s)


@router.put("/schemes/{sid}")
@limiter.limit("30/minute")
def update_scheme(request: Request, sid: str, body: dict, user=Depends(require("super_admin"))):
    s = db.schemes.find_one({"_id": oid(sid)})
    if not s:
        raise HTTPException(444, detail={"detail": "Not found", "code": "NOT_FOUND"})
    if "code" in body and body["code"] != s["code"]:
        if db.schemes.find_one({"code": body["code"]}):
            raise HTTPException(409, detail={"detail": "Scheme code exists", "code": "CODE_EXISTS"})
    now = datetime.now(timezone.utc)
    db.schemes.update_one({"_id": s["_id"]}, {"$set": {**body, "updated_at": now}})
    audit(user, "scheme.update", "scheme", sid, body)
    return ser({**s, **body, "updated_at": now})


@router.get("/schemes/{sid}/templates")
@limiter.limit("100/minute")
def list_templates(
    request: Request, sid: str, active_only: bool = True, user=Depends(require(*GOVT_READ))
):
    q = {"scheme_id": oid(sid)}
    if active_only:
        q["active"] = True
    return ser(list(db.trade_templates.find(q).sort("trade_name", 1)))


@router.post("/schemes/{sid}/templates")
@limiter.limit("30/minute")
def create_template(request: Request, sid: str, body: dict, user=Depends(require("super_admin"))):
    if not db.schemes.find_one({"_id": oid(sid)}):
        raise HTTPException(404, detail={"detail": "Scheme not found", "code": "NOT_FOUND"})
    now = datetime.now(timezone.utc)
    if db.trade_templates.find_one({"scheme_id": oid(sid), "trade_name": body["trade_name"]}):
        raise HTTPException(409, detail={"detail": "Template name exists", "code": "NAME_EXISTS"})
    doc = {
        "scheme_id": oid(sid),
        "trade_name": body["trade_name"],
        "detector_classes": body["detector_classes"],
        "equipment_rules": body["equipment_rules"],
        "presence_rule": body.get("presence_rule", "seated"),
        "count_tolerance": body.get("count_tolerance", 1),
        "agree_ratio": body.get("agree_ratio", 0.6),
        "min_confidence": body.get("min_confidence", 0.35),
        "base_frames": body.get("base_frames", 10),
        "step_frames": body.get("step_frames", 5),
        "max_frames": body.get("max_frames", 40),
        "active": body.get("active", True),
        "created_at": now,
        "updated_at": now,
    }
    doc["_id"] = db.trade_templates.insert_one(doc).inserted_id
    audit(
        user,
        "template.create",
        "trade_template",
        str(doc["_id"]),
        {"trade_name": doc["trade_name"]},
    )
    return ser(doc)


@router.get("/templates/{tid}")
@limiter.limit("100/minute")
def get_template(request: Request, tid: str, user=Depends(require(*GOVT_READ))):
    t = db.trade_templates.find_one({"_id": oid(tid)})
    if not t:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    return ser(t)


@router.put("/templates/{tid}")
@limiter.limit("30/minute")
def update_template(request: Request, tid: str, body: dict, user=Depends(require("super_admin"))):
    t = db.trade_templates.find_one({"_id": oid(tid)})
    if not t:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    if "trade_name" in body and body["trade_name"] != t["trade_name"]:
        if db.trade_templates.find_one(
            {"scheme_id": t["scheme_id"], "trade_name": body["trade_name"]}
        ):
            raise HTTPException(
                409, detail={"detail": "Template name exists", "code": "NAME_EXISTS"}
            )
    now = datetime.now(timezone.utc)
    db.trade_templates.update_one({"_id": t["_id"]}, {"$set": {**body, "updated_at": now}})
    audit(user, "template.update", "trade_template", tid, body)
    return ser({**t, **body, "updated_at": now})
