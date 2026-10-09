import gridfs
from bson import Binary
from datetime import datetime, timezone
from fastapi import APIRouter, UploadFile, HTTPException, Response, Form
from app.db.mongo import db, oid, verify_file_sig

router = APIRouter()
fs = gridfs.GridFS(db, collection="documents")
ALLOWED = {"application/pdf", "image/jpeg", "image/png"}


def save_evidence(centre_id, jpeg: bytes, eid: str, session_id=None):
    """eid deterministic (ledger hash prefix + index) so a re-sync cannot duplicate."""
    doc = {
        "_id": eid,
        "centre_id": centre_id,
        "session_id": session_id,
        "data": Binary(jpeg),
        "created_at": datetime.now(timezone.utc)
    }
    if session_id is not None:
        doc["session_id"] = session_id
    db.evidence_blobs.replace_one({"_id": eid}, doc, upsert=True)
    return eid


@router.get("/files/{kind}/{fid}")
def get_file(kind: str, fid: str, exp: int = 0, sig: str = ""):
    if kind not in ("evidence", "document") or not verify_file_sig(kind, fid, exp, sig):
        raise HTTPException(403, detail={"detail": "Link expired", "code": "LINK_EXPIRED"})
    if kind == "evidence":
        d = db.evidence_blobs.find_one({"_id": fid})
        if not d:
            raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
        return Response(bytes(d["data"]), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
    g = fs.get(oid(fid))
    return Response(g.read(), media_type=g.content_type or "application/octet-stream",
                    headers={"Cache-Control": "private, no-store"})


@router.post("/public/applications/{code}/documents")
async def upload_doc(code: str, phone: str = Form(...), file: UploadFile = None):
    a = db.applications.find_one({"tracking_code": code, "contact_phone": phone})
    if not a or len(a.get("documents") or []) >= 5:
        raise HTTPException(404, detail={"detail": "Not found or limit reached", "code": "NOT_FOUND"})
    if file.content_type not in ALLOWED:
        raise HTTPException(422, detail={"detail": "PDF, JPG or PNG only", "code": "BAD_FILE"})
    data = await file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(422, detail={"detail": "Max 5 MB", "code": "FILE_TOO_LARGE"})
    fid = fs.put(data, filename=file.filename, content_type=file.content_type)
    db.applications.update_one({"_id": a["_id"]}, {"$push": {"documents": {
        "name": file.filename,
        "file_id": str(fid),
        "size": len(data),
        "content_type": file.content_type
    }}})
    return {"file_id": str(fid)}