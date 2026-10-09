import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.deps import require
from app.core.limiter import limiter
from app.db.mongo import db, oid, ser, sign_file_url
from app.services.dbfuncs import attendance_series

router = APIRouter()
GOVT_READ = ("super_admin", "scheme_officer", "auditor")


@router.get("/centres/{cid}/attendance")
@limiter.limit("100/minute")
def centre_attendance(
    request: Request,
    cid: str,
    from_date: str,
    to_date: str,
    user=Depends(require(*GOVT_READ)),
):
    centre = db.centres.find_one({"_id": oid(cid)})
    if not centre:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    return attendance_series(oid(cid), from_date, to_date)


@router.get("/centres/{cid}/equipment")
@limiter.limit("100/minute")
def centre_equipment(request: Request, cid: str, user=Depends(require(*GOVT_READ))):
    centre = db.centres.find_one({"_id": oid(cid)})
    if not centre:
        raise HTTPException(444, detail={"detail": "Not found", "code": "NOT_FOUND"})
    tpl = db.trade_templates.find_one({"_id": centre["trade_template_id"]})
    rules = tpl.get("equipment_rules", []) if tpl else []
    eq_map = {e["class"]: e for e in centre.get("equipment", [])}
    out = []
    for r in rules:
        eq = eq_map.get(r["class"], {})
        obs = (
            db.observations.find(
                {"centre_id": oid(cid), "equipment": {"$ne": None}},
                {"equipment": 1, "created_at": 1},
            )
            .sort("created_at", -1)
            .limit(1)
        )
        obs = list(obs)
        obs_eq = obs[0].get("equipment", {}).get(r["class"], {}) if obs else {}
        out.append(
            {
                "class": r["class"],
                "label": r["label"],
                "sanctioned_qty": eq.get("sanctioned_qty", 0),
                "declared_qty": eq.get("declared_qty", 0),
                "observed": obs_eq.get("observed"),
                "ci_low": obs_eq.get("ci_low"),
                "ci_high": obs_eq.get("ci_high"),
                "operability": r.get("operability", "none"),
                "required": r.get("required", False),
            }
        )
    return ser(out)


@router.get("/centres/{cid}/evidence")
@limiter.limit("100/minute")
def centre_evidence(
    request: Request,
    cid: str,
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require(*GOVT_READ)),
):

    centre = db.centres.find_one({"_id": oid(cid)})
    if not centre:
        raise HTTPException(444, detail={"detail": "Not found", "code": "NOT_FOUND"})
    q = {"centre_id": oid(cid)}
    if cursor:
        q["_id"] = {"$lt": oid(cursor)}
    items = list(db.evidence_blobs.find(q).sort("created_at", -1).limit(limit + 1))
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    out = []
    for d in items:
        out.append(
            {
                "id": d["_id"],
                "kind": d.get("kind", "evidence"),
                "session_id": str(d["session_id"]) if d.get("session_id") else None,
                "url": sign_file_url("evidence", d["_id"]),
                "created_at": d["created_at"],
            }
        )
    return {"items": ser(out), "next_cursor": next_cursor}


@router.get("/centres/{cid}/timeline")
@limiter.limit("100/minute")
def centre_timeline(
    request: Request,
    cid: str,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = Query(50, le=200),
    cursor: str | None = None,
    user=Depends(require(*GOVT_READ)),
):
    centre = db.centres.find_one({"_id": oid(cid)})
    if not centre:
        raise HTTPException(444, detail={"detail": "Not found", "code": "NOT_FOUND"})
    q = {"entity_id": str(cid)}
    if from_date:
        q.setdefault("ts", {})["$gte"] = datetime.fromisoformat(from_date)
    if to_date:
        q.setdefault("ts", {})["$lte"] = datetime.fromisoformat(to_date)
    if cursor:
        q["_id"] = {"$lt": oid(cursor)}
    items = list(db.audit_log.find(q).sort("ts", -1).limit(limit + 1))
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    return {"items": ser(items), "next_cursor": next_cursor}


@router.get("/reports/rollup")
@limiter.limit("100/minute")
def rollup(
    request: Request,
    by: str = "district",
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require(*GOVT_READ)),
):
    if by not in ("district", "scheme", "trade"):
        raise HTTPException(422, detail={"detail": "Invalid by", "code": "INVALID_BY"})
    # Simplified rollup - would use aggregation in production
    centres = list(
        db.centres.find({"status": {"$in": ["live", "pending_setup", "awaiting_camera_approval"]}})
    )
    groups = {}
    for c in centres:
        key = c.get(by, "unknown")
        g = groups.setdefault(key, {"count": 0, "open_flags": 0, "risk_avg": 0})
        g["count"] += 1
        g["open_flags"] += db.flags.count_documents(
            {
                "centre_id": c["_id"],
                "status": {
                    "$in": ["open", "acknowledged", "centre_responded", "under_review", "escalated"]
                },
            }
        )
        g["risk_avg"] += c.get("risk_score", 0)
    for g in groups.values():
        g["risk_avg"] = round(g["risk_avg"] / g["count"], 2) if g["count"] else 0
    items = [{"key": k, **v} for k, v in groups.items()]
    items.sort(key=lambda x: x["risk_avg"], reverse=True)
    return ser(items)


@router.get("/audit-log")
@limiter.limit("100/minute")
def audit_log(
    request: Request,
    actor: str | None = None,
    entity: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = Query(50, le=200),
    cursor: str | None = None,
    user=Depends(require("super_admin", "auditor")),
):
    q = {}
    if actor:
        q["actor"] = oid(actor)
    if entity:
        q["entity"] = entity
    if from_date:
        q.setdefault("ts", {})["$gte"] = datetime.fromisoformat(from_date)
    if to_date:
        q.setdefault("ts", {})["$lte"] = datetime.fromisoformat(to_date)
    if cursor:
        q["_id"] = {"$lt": oid(cursor)}
    items = list(db.audit_log.find(q).sort("ts", -1).limit(limit + 1))
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    return {"items": ser(items), "next_cursor": next_cursor}


@router.get("/accuracy")
@limiter.limit("100/minute")
def accuracy(request: Request, user=Depends(require(*GOVT_READ))):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "eval", "results.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"message": "No eval results found. Run eval/evaluate.py"}
