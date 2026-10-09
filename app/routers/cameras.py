from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.audit import audit
from app.core.deps import require
from app.core.limiter import limiter
from app.db.mongo import db, oid, ser, sign_file_url

router = APIRouter()
GOVT_WRITE = ("super_admin", "scheme_officer")
GOVT_READ = (*GOVT_WRITE, "auditor")


def _validate_polygon(poly: list[list[float]]) -> None:
    if len(poly) < 3:
        raise HTTPException(
            422, detail={"detail": "Polygon needs >=3 points", "code": "BAD_POLYGON"}
        )
    for pt in poly:
        if not (0 <= pt[0] <= 1 and 0 <= pt[1] <= 1):
            raise HTTPException(
                422, detail={"detail": "Coordinates must be 0..1", "code": "BAD_COORD"}
            )


@router.get("/cameras")
@limiter.limit("100/minute")
def list_cameras(
    request: Request,
    status: str | None = None,
    setup_status: str | None = None,
    limit: int = Query(25, le=100),
    cursor: str | None = None,
    user=Depends(require(*GOVT_READ)),
):
    q = {}
    if status:
        q["status"] = status
    if setup_status:
        q["setup_status"] = setup_status
    if cursor:
        q["_id"] = {"$lt": oid(cursor)}
    items = list(db.cameras.find(q).sort("_id", -1).limit(limit + 1))
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = str(items[-1]["_id"])
    return {"items": ser(items), "next_cursor": next_cursor}


@router.post("/my/cameras")
@limiter.limit("30/minute")
def create_camera(request: Request, body: dict, user=Depends(require("centre_admin"))):
    cid = user["centre_id"]
    now = datetime.now(timezone.utc)
    doc = {
        "centre_id": cid,
        "name": body["name"],
        "source_uri": body["source_uri"],
        "resolution": body.get("resolution"),
        "roi_polygon": None,
        "zones": None,
        "reference_phash": None,
        "setup_status": "draft",
        "status": "unknown",
        "created_at": now,
    }
    doc["_id"] = db.cameras.insert_one(doc).inserted_id
    audit(user, "camera.create", "camera", str(doc["_id"]), {"name": doc["name"]})
    return ser(doc)


@router.get("/my/cameras")
@limiter.limit("100/minute")
def list_my_cameras(request: Request, user=Depends(require("centre_admin", "trainer"))):
    cid = user["centre_id"]
    return ser(list(db.cameras.find({"centre_id": cid}).sort("created_at", -1)))


@router.get("/my/cameras/{cam_id}")
@limiter.limit("100/minute")
def get_camera(request: Request, cam_id: str, user=Depends(require("centre_admin", "trainer"))):
    c = db.cameras.find_one({"_id": oid(cam_id)})
    if not c or str(c["centre_id"]) != str(user["centre_id"]):
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    return ser(c)


@router.put("/my/cameras/{cam_id}/setup")
@limiter.limit("30/minute")
def update_camera_setup(
    request: Request, cam_id: str, body: dict, user=Depends(require("centre_admin"))
):
    c = db.cameras.find_one({"_id": oid(cam_id)})
    if not c or str(c["centre_id"]) != str(user["centre_id"]):
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    if c["setup_status"] in ("approved", "submitted"):
        raise HTTPException(
            409, detail={"detail": "Cannot edit after submission", "code": "BAD_STATE"}
        )

    roi = body.get("roi_polygon")
    zones = body.get("zones")
    if roi:
        _validate_polygon(roi)
    if zones:
        for z in zones:
            _validate_polygon(z["polygon"])

    now = datetime.now(timezone.utc)
    db.cameras.update_one(
        {"_id": c["_id"]},
        {"$set": {"roi_polygon": roi, "zones": zones, "updated_at": now}},
    )
    audit(user, "camera.setup", "camera", cam_id, {"roi": bool(roi), "zones": bool(zones)})
    return ser({**c, "roi_polygon": roi, "zones": zones, "updated_at": now})


@router.get("/my/cameras/{cam_id}/reference-frame")
@limiter.limit("100/minute")
def get_reference_frame(
    request: Request, cam_id: str, user=Depends(require("centre_admin", "trainer"))
):
    c = db.cameras.find_one({"_id": oid(cam_id)})
    if not c or str(c["centre_id"]) != str(user["centre_id"]):
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    if not c.get("reference_blob_id"):
        raise HTTPException(404, detail={"detail": "No reference frame", "code": "NOT_FOUND"})

    url = sign_file_url("evidence", c["reference_blob_id"])
    return {
        "url": url,
        "width": c.get("reference_width"),
        "height": c.get("reference_height"),
        "quality": c.get("reference_quality"),
    }


@router.post("/my/cameras/{cam_id}/submit")
@limiter.limit("30/minute")
def submit_camera(request: Request, cam_id: str, user=Depends(require("centre_admin"))):
    c = db.cameras.find_one({"_id": oid(cam_id)})
    if not c or str(c["centre_id"]) != str(user["centre_id"]):
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    if not c.get("roi_polygon"):
        raise HTTPException(409, detail={"detail": "ROI not set", "code": "NO_ROI"})
    now = datetime.now(timezone.utc)
    db.cameras.update_one(
        {"_id": c["_id"]},
        {"$set": {"setup_status": "submitted", "submitted_at": now}},
    )
    audit(user, "camera.submit", "camera", cam_id, {})
    return {"setup_status": "submitted"}


@router.post("/cameras/{cam_id}/approve")
@limiter.limit("30/minute")
def approve_camera(request: Request, cam_id: str, user=Depends(require(*GOVT_WRITE))):
    c = db.cameras.find_one({"_id": oid(cam_id)})
    if not c:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    if c["setup_status"] != "submitted":
        raise HTTPException(409, detail={"detail": "Not submitted", "code": "BAD_STATE"})
    now = datetime.now(timezone.utc)
    db.cameras.update_one(
        {"_id": c["_id"]},
        {"$set": {"setup_status": "approved", "decided_by": oid(user["id"]), "decided_at": now}},
    )
    centre = db.centres.find_one({"_id": c["centre_id"]})
    if centre and centre["status"] == "awaiting_camera_approval":
        other_cams = db.cameras.find({"centre_id": c["centre_id"], "setup_status": "approved"})
        if all(cam["setup_status"] == "approved" for cam in other_cams):
            db.centres.update_one(
                {"_id": c["centre_id"]}, {"$set": {"status": "live", "live_since": now}}
            )
    audit(user, "camera.approve", "camera", cam_id, {})
    return {"setup_status": "approved"}


@router.post("/cameras/{cam_id}/reject")
@limiter.limit("30/minute")
def reject_camera(request: Request, cam_id: str, body: dict, user=Depends(require(*GOVT_WRITE))):
    c = db.cameras.find_one({"_id": oid(cam_id)})
    if not c:
        raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
    if c["setup_status"] != "submitted":
        raise HTTPException(409, detail={"detail": "Not submitted", "code": "BAD_STATE"})
    reason = body.get("reason", "")
    if not reason:
        raise HTTPException(422, detail={"detail": "Reason required", "code": "NO_REASON"})
    now = datetime.now(timezone.utc)
    db.cameras.update_one(
        {"_id": c["_id"]},
        {
            "$set": {
                "setup_status": "rejected",
                "reject_reason": reason,
                "decided_by": oid(user["id"]),
                "decided_at": now,
            }
        },
    )
    audit(user, "camera.reject", "camera", cam_id, {"reason": reason})
    return {"setup_status": "rejected"}
