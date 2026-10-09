import glob
import os
from datetime import datetime, timezone

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.deps import require
from app.core.limiter import limiter
from app.db.mongo import db, oid, ser, sign_file_url
from app.services.dbfuncs import recompute_risk
from app.services.flag_engine import evaluate
from app.services.ledger import append, unsynced_count
from app.services.pipeline import analyse_hour
from app.services.sync import STATE, sync_tick
from app.services.vision.detector import Detector

router = APIRouter()


@router.get("/edge/videos")
@limiter.limit("100/minute")
def list_videos(request: Request, user=Depends(require("centre_admin", "super_admin"))):
    vids = []
    for f in glob.glob("sample_videos/*.mp4"):
        vids.append(os.path.basename(f))
    return {"videos": vids}


@router.post("/edge/demo/run")
@limiter.limit("30/minute")
def run_demo(request: Request, body: dict, user=Depends(require("centre_admin", "super_admin"))):
    camera_id = body["camera_id"]
    session_id = body["session_id"]
    video_name = body["video"]
    _ = body.get("scenario", "normal")  # reserved for future use
    claimed_override = body.get("claimed_override")

    cam = db.cameras.find_one({"_id": oid(camera_id)})
    if not cam:
        raise HTTPException(404, detail={"detail": "Camera not found", "code": "NOT_FOUND"})
    if str(cam["centre_id"]) != str(user["centre_id"]) and user["role"] != "super_admin":
        raise HTTPException(403, detail={"detail": "Not your centre", "code": "WRONG_CENTRE"})

    centre = db.centres.find_one({"_id": cam["centre_id"]})
    tpl = db.trade_templates.find_one({"_id": centre["trade_template_id"]})
    session = db.sessions.find_one({"_id": oid(session_id)})
    if not session:
        raise HTTPException(404, detail={"detail": "Session not found", "code": "NOT_FOUND"})

    staff_verified = (
        db.staff_attendance.count_documents({"session_id": oid(session_id), "verified": True}) > 0
    )
    staff_subtract = 0 if staff_verified else 1

    detector = Detector(tpl["detector_classes"], float(tpl["min_confidence"]))
    video_path = f"sample_videos/{video_name}"
    if not os.path.exists(video_path):
        raise HTTPException(404, detail={"detail": "Video not found", "code": "NOT_FOUND"})

    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    def grab_frame():
        frame_idx = int(total_frames * (hash(str(datetime.now(timezone.utc))) % 1000) / 1000)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        return frame if ret else None

    ref_frame = None
    if cam.get("reference_blob_id"):
        d = db.evidence_blobs.find_one({"_id": cam["reference_blob_id"]})
        if d:
            ref_frame = cv2.imdecode(np.frombuffer(d["data"], np.uint8), cv2.IMREAD_COLOR)

    res = analyse_hour(grab_frame, detector, tpl, cam, staff_subtract, ref_frame)
    cap.release()

    claimed = (
        claimed_override
        if claimed_override is not None
        else (
            session.get("attendance_claims", [{}])[0].get("claimed_count")
            if session.get("attendance_claims")
            else None
        )
    )

    ctx = {
        "claimed": claimed,
        "enrolled": db.trainees.count_documents({"centre_id": centre["_id"], "active": True}),
        "sanctioned_max": centre["max_trainees"],
        "tol": tpl["count_tolerance"],
        "obs": res,
        "equipment_rules": tpl["equipment_rules"],
        "repairs": {
            r["equipment_class"]: r["qty"]
            for r in db.repair_declarations.find({"centre_id": centre["_id"], "status": "approved"})
        },
        "repeats": {},
        "trainer_verified": staff_verified,
        "camera_tamper": res["tamper"],
    }
    flags = evaluate(ctx)

    for fl in flags:
        fl["centre_id"] = centre["_id"]
        fl["session_id"] = session["_id"]
        fl["observation_id"] = res.get("observation_id")
        fl["evidence_ids"] = [res.get("thumbs", [])[0]] if res.get("thumbs") else []
        fl["model_version"] = detector.version
        fl["status"] = "open"
        fl["created_at"] = datetime.now(timezone.utc)
        fl["events"] = [{"action": "created", "ts": datetime.now(timezone.utc)}]
        ledger_entry = append(
            "flag", {"row": fl, "centre_id": str(centre["_id"]), "evidence_local": []}
        )
        fl["ledger_hash"] = ledger_entry["hash"]
        db.flags.insert_one(fl)
        recompute_risk(centre["_id"])

    thumb_urls = [sign_file_url("evidence", h) for h in res.get("thumbs", [])]

    return {
        "observed": res["observed"],
        "ci_low": res["ci_low"],
        "ci_high": res["ci_high"],
        "state": res["state"],
        "per_frame": res["per_frame"],
        "frames_used": res["frames_used"],
        "claimed": claimed,
        "thumbnails": thumb_urls,
        "flags": ser(flags),
    }


@router.post("/edge/offline")
@limiter.limit("30/minute")
def set_offline(request: Request, body: dict, user=Depends(require("centre_admin", "super_admin"))):
    STATE["offline"] = bool(body.get("value", False))
    return {"offline": STATE["offline"]}


@router.post("/edge/sync-now")
@limiter.limit("30/minute")
def sync_now(request: Request, user=Depends(require("centre_admin", "super_admin"))):
    return sync_tick(batch=100)


@router.get("/edge/status")
@limiter.limit("100/minute")
def edge_status(request: Request, user=Depends(require("centre_admin", "super_admin"))):
    device = db.edge_devices.find_one({"_id": "edge-001"})
    return {
        "online": not STATE["offline"],
        "unsynced_count": unsynced_count(),
        "last_sync": device.get("last_sync") if device else None,
        "chain_ok": device.get("chain_ok", True) if device else True,
    }
