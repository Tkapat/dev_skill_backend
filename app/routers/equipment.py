from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.audit import audit
from app.core.deps import require
from app.core.limiter import limiter
from app.db.mongo import db, oid, ser

router = APIRouter()


@router.get("/centres/{cid}/equipment")
@limiter.limit("100/minute")
def list_equipment(
    request: Request, cid: str, user=Depends(require("super_admin", "scheme_officer", "auditor"))
):
    centre = db.centres.find_one({"_id": oid(cid)})
    if not centre:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    tpl = db.trade_templates.find_one({"_id": centre["trade_template_id"]})
    rules = tpl.get("equipment_rules", []) if tpl else []
    eq_map = {e["class"]: e for e in centre.get("equipment", [])}
    out = []
    for r in rules:
        eq = eq_map.get(r["class"], {})
        out.append(
            {
                "class": r["class"],
                "label": r["label"],
                "sanctioned": r.get("min_units_per_trainee", 0),
                "declared": eq.get("declared_qty", 0),
                "observed": None,
                "operability": r.get("operability", "none"),
                "required": r.get("required", False),
            }
        )
    return ser(out)


@router.get("/my/equipment")
@limiter.limit("100/minute")
def list_my_equipment(request: Request, user=Depends(require("centre_admin", "trainer"))):
    cid = user["centre_id"]
    centre = db.centres.find_one({"_id": cid})
    if not centre:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    return ser(centre.get("equipment", []))


@router.put("/my/equipment")
@limiter.limit("30/minute")
def update_declared_equipment(request: Request, body: dict, user=Depends(require("centre_admin"))):
    cid = user["centre_id"]
    centre = db.centres.find_one({"_id": cid})
    if not centre:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    if centre["status"] in ("suspended", "terminated"):
        raise HTTPException(403, detail={"detail": "Centre not active", "code": "CENTRE_INACTIVE"})

    equipment = body.get("equipment", [])
    for e in equipment:
        db.centres.update_one(
            {"_id": cid, "equipment.class": e["class"]},
            {
                "$set": {
                    "equipment.$.declared_qty": e["declared_qty"],
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
    audit(user, "equipment.update_declared", "centre", cid, {"equipment": equipment})
    return {"ok": True}
