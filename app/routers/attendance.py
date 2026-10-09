from fastapi import APIRouter, Depends, HTTPException
from app.core.deps import require
from app.db.mongo import db, oid, ser
from app.services import ledger
from app.core.audit import audit
from app.services.face import decode, verify

router = APIRouter()


@router.post("/my/sessions/{sid}/claim")
def claim(sid: str, body: dict, user=Depends(require("trainer", "centre_admin"))):
    s = db.sessions.find_one({"_id": oid(sid)})
    if not s or str(s["centre_id"]) != str(user["centre_id"]):
        raise HTTPException(403, detail={"detail": "Wrong centre", "code": "WRONG_CENTRE"})
    ids = body["present_trainee_ids"]
    existing = db.attendance_claims.find_one({"session_id": oid(sid)})
    if existing and not body.get("edit_reason"):
        raise HTTPException(409, detail={"detail": "Already submitted. Provide edit_reason.", "code": "CLAIM_LOCKED"})
    row = {
        "session_id": oid(sid),
        "centre_id": s["centre_id"],
        "claimed_count": len(ids),
        "present_trainee_ids": [oid(i) for i in ids],
        "marked_by": oid(user["id"]),
        "edit_reason": body.get("edit_reason")
    }
    db.attendance_claims.update_one(
        {"session_id": oid(sid)},
        {"$set": row},
        upsert=True
    )
    ledger.append("claim", {"centre_id": str(s["centre_id"]), "session_id": str(sid), "claimed": len(ids)})
    audit(user, "attendance.claim", "session", sid, {"claimed": len(ids)})
    return {"claimed_count": len(ids)}


@router.post("/my/sessions/{sid}/staff-scan")
def staff_scan(sid: str, body: dict, user=Depends(require("trainer"))):
    s = db.sessions.find_one({"_id": oid(sid)})
    if not s or str(s["centre_id"]) != str(user["centre_id"]):
        raise HTTPException(403, detail={"detail": "Wrong centre", "code": "WRONG_CENTRE"})
    staff = db.staff.find_one({"user_id": oid(user["id"])})
    if not staff:
        raise HTTPException(404, detail={"detail": "Staff not found", "code": "STAFF_NOT_FOUND"})
    if not staff.get("face_enrolled"):
        raise HTTPException(400, detail={"detail": "Face not enrolled", "code": "FACE_NOT_ENROLLED"})
    img = decode(body["image_b64"])
    face = db.staff_faces.find_one({"_id": staff["_id"]})
    verified, score = verify(img, bytes(face["embedding_enc"]))
    db.staff_attendance.update_one(
        {"session_id": oid(sid), "staff_id": staff["_id"]},
        {"$set": {"method": "face", "verified": verified, "score": score}},
        upsert=True
    )
    ledger.append("staff_scan", {"centre_id": str(s["centre_id"]), "session_id": str(sid), "staff_id": str(staff["_id"]), "verified": verified, "score": score})
    return {"verified": verified, "score": score}