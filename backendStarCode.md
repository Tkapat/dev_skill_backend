==================================================================
 dev_skill_backend: KEY CODE (copy as starting point; fill routine
 CRUD routers following the same patterns). Each "# FILE:" is a file.
==================================================================

# FILE: app/core/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    supabase_url: str
    supabase_service_role_key: str
    fernet_key: str
    edge_signing_key_hex: str
    device_id: str = "edge-001"
    allowed_origins: str = "http://localhost:3000,http://localhost:3001"
    local_db_path: str = "data/edge.db"
    evidence_dir: str = "data/evidence"
    force_offline: bool = False
    model_config = {"env_file": ".env", "extra": "ignore"}

settings = Settings()

# FILE: app/db/supabase_client.py
from supabase import create_client
from app.core.config import settings
sb = create_client(settings.supabase_url, settings.supabase_service_role_key)

# FILE: app/core/deps.py
from datetime import datetime, timezone
from cachetools import TTLCache
from fastapi import Depends, Header, HTTPException
from app.db.supabase_client import sb

_cache = TTLCache(maxsize=512, ttl=60)
CENTRE_ROLES = {"centre_admin", "trainer"}

def invalidate_cache():
    _cache.clear()

def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, detail={"detail": "Missing token", "code": "NO_TOKEN"})
    token = authorization[7:]
    if token in _cache:
        return _cache[token]
    try:
        user = sb.auth.get_user(token).user
    except Exception:
        raise HTTPException(401, detail={"detail": "Invalid token", "code": "BAD_TOKEN"})
    if not user:
        raise HTTPException(401, detail={"detail": "Invalid token", "code": "BAD_TOKEN"})
    prof = sb.table("profiles").select("*").eq("id", user.id).single().execute().data
    if not prof or not prof["active"]:
        raise HTTPException(403, detail={"detail": "Account disabled", "code": "DISABLED"})
    _cache[token] = prof
    return prof

def require(*roles, allow_unchanged_password=False):
    def dep(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, detail={"detail": "Forbidden", "code": "FORBIDDEN"})
        if user["must_change_password"] and not allow_unchanged_password:
            exp = user.get("temp_password_expires_at")
            if exp and datetime.fromisoformat(exp.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                raise HTTPException(403, detail={"detail": "Credentials expired", "code": "CREDENTIALS_EXPIRED"})
            raise HTTPException(403, detail={"detail": "Change password first", "code": "PASSWORD_CHANGE_REQUIRED"})
        return user
    return dep

def assert_centre_access(user, centre_id: str):
    if user["role"] in CENTRE_ROLES and str(user.get("centre_id")) != str(centre_id):
        raise HTTPException(403, detail={"detail": "Not your centre", "code": "WRONG_CENTRE"})

# FILE: app/core/audit.py
from app.db.supabase_client import sb
def audit(user, action, entity, entity_id, meta=None):
    sb.table("audit_log").insert({"actor": user["id"], "actor_role": user["role"],
        "action": action, "entity": entity, "entity_id": str(entity_id), "meta": meta or {}}).execute()

# FILE: app/services/credentials.py
import secrets, string
from datetime import datetime, timedelta, timezone
from app.db.supabase_client import sb

DOMAIN = "centres.devskill.in"
def login_email(login_id: str) -> str:
    return f"{login_id.strip().lower()}@{DOMAIN}"

def gen_password(n: int = 12) -> str:
    sets = [string.ascii_lowercase, string.ascii_uppercase, string.digits, "@#$%&*"]
    pool = "".join(sets)
    while True:
        p = "".join(secrets.choice(pool) for _ in range(n))
        if all(any(c in s for c in p) for s in sets):
            return p

def create_login(login_id: str, password: str, role: str, centre_id: str, full_name: str) -> str:
    res = sb.auth.admin.create_user({"email": login_email(login_id), "password": password,
                                     "email_confirm": True})
    uid = res.user.id
    try:
        sb.table("profiles").insert({"id": uid, "role": role, "centre_id": centre_id,
            "full_name": full_name, "must_change_password": True,
            "temp_password_expires_at": (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()
        }).execute()
    except Exception:
        sb.auth.admin.delete_user(uid)   # rollback
        raise
    return uid

# FILE: app/routers/applications.py  (approve flow = most important govt action)
from fastapi import APIRouter, Depends, HTTPException
from app.core.deps import require
from app.core.audit import audit
from app.db.supabase_client import sb
from app.services.credentials import gen_password, create_login

router = APIRouter()
GOVT_WRITE = ("super_admin", "scheme_officer")

@router.post("/applications/{app_id}/approve")
def approve(app_id: str, user=Depends(require(*GOVT_WRITE))):
    a = sb.table("applications").select("*").eq("id", app_id).single().execute().data
    if not a or a["status"] not in ("submitted", "under_review", "resubmitted"):
        raise HTTPException(409, detail={"detail": "Not approvable", "code": "BAD_STATE"})
    code = sb.rpc("next_centre_code").execute().data
    centre = sb.table("centres").insert({"code": code, "application_id": a["id"], "name": a["centre_name"],
        "scheme_id": a["scheme_id"], "trade_template_id": a["trade_template_id"],
        "max_trainees": a["max_trainees"], "address": a["address"], "district": a["district"],
        "state": a["state"], "lat": a["lat"], "lng": a["lng"],
        "contact_phone": a["contact_phone"], "contact_email": a["contact_email"]}).execute().data[0]
    try:
        sb.table("centre_equipment").insert([{"centre_id": centre["id"], "class": e["class"],
            "label": e.get("label", e["class"]), "sanctioned_qty": e["qty"]} for e in a["equipment"]]).execute()
        pwd = gen_password()
        create_login(code, pwd, "centre_admin", centre["id"], a["centre_name"] + " Admin")
    except Exception:
        sb.table("centres").delete().eq("id", centre["id"]).execute()
        raise
    sb.table("applications").update({"status": "approved", "reviewed_by": user["id"],
        "centre_id": centre["id"]}).eq("id", app_id).execute()
    audit(user, "application.approve", "application", app_id, {"centre_code": code})
    return {"centre_id": centre["id"], "login_id": code, "temporary_password": pwd,
            "expires_in_hours": 72, "note": "Shown once. Share securely."}

# FILE: app/services/plausibility.py
import math
def plausibility(max_trainees: int, equipment: list, rules: list) -> list[str]:
    qty = {e["class"]: e["qty"] for e in equipment}
    warns = []
    for r in rules:
        if not r.get("required"): continue
        need = math.ceil(max_trainees * r["min_units_per_trainee"])
        have = qty.get(r["class"], 0)
        if have < need:
            warns.append(f"{r['label']}: {have} listed, template expects at least {need} for {max_trainees} trainees")
    return warns

# FILE: app/routers/trainees.py  (enrolment cap, API-level check)
from fastapi import APIRouter, Depends, HTTPException
from app.core.deps import require, assert_centre_access
from app.db.supabase_client import sb
router = APIRouter()

@router.post("/my/trainees")
def enrol(body: dict, user=Depends(require("centre_admin"))):
    cid = user["centre_id"]
    mx = sb.table("centres").select("max_trainees,status").eq("id", cid).single().execute().data
    if mx["status"] in ("suspended", "terminated"):
        raise HTTPException(403, detail={"detail": "Centre not active", "code": "CENTRE_INACTIVE"})
    cnt = sb.table("trainees").select("id", count="exact").eq("centre_id", cid).eq("active", True).execute().count
    if cnt >= mx["max_trainees"]:
        raise HTTPException(409, detail={"detail": "Sanctioned limit reached. Request extra enrolment.", "code": "ENROLMENT_CAP_REACHED"})
    return sb.table("trainees").insert({"centre_id": cid, "batch_id": body.get("batch_id"),
        "full_name": body["full_name"], "external_id": body.get("external_id")}).execute().data[0]

# FILE: app/services/vision/detector.py
import numpy as np, torch
from torchvision.ops import batched_nms
from ultralytics import YOLO, YOLOWorld

COCO = {"person","chair","laptop","tv","keyboard","mouse","cell phone","bench","couch","dining table","bottle","book"}

class Detector:
    def __init__(self, classes: list[str], conf: float = 0.35, tiling: bool = True):
        self.classes, self.conf, self.tiling = classes, conf, tiling
        if all(c in COCO for c in classes):
            self.model = YOLO("yolo11n.pt"); self.version = "yolo11n"
        else:
            self.model = YOLOWorld("yolov8s-worldv2.pt")
            self.model.set_classes(classes);  self.version = "yolov8s-worldv2"
        self.names = None

    def _predict(self, img, ox=0, oy=0):
        r = self.model.predict(img, conf=self.conf, verbose=False)[0]
        names = r.names
        out = []
        for b in r.boxes:
            name = names[int(b.cls)]
            if name not in self.classes: continue
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            out.append((x1 + ox, y1 + oy, x2 + ox, y2 + oy, float(b.conf), name))
        return out

    def detect(self, frame: np.ndarray):
        h, w = frame.shape[:2]
        dets = self._predict(frame)
        if self.tiling:
            tw, th, ov = int(w * 0.6), int(h * 0.6), 0.2
            for ox in (0, w - tw):
                for oy in (0, h - th):
                    dets += self._predict(frame[oy:oy + th, ox:ox + tw], ox, oy)
        if not dets: return []
        boxes = torch.tensor([d[:4] for d in dets]); scores = torch.tensor([d[4] for d in dets])
        cls_ids = torch.tensor([self.classes.index(d[5]) for d in dets])
        keep = batched_nms(boxes, scores, cls_ids, 0.5).tolist()
        return [dets[i] for i in keep]   # (x1,y1,x2,y2,conf,name)

# FILE: app/services/vision/quality.py
import cv2, numpy as np

def quality_gate(frame, prev_gray=None, prev_gap_s=0, ref_gray=None):
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    reasons = []
    if g.mean() < 25: reasons.append("dark")
    if g.std() < 12: reasons.append("covered")
    if cv2.Laplacian(g, cv2.CV_64F).var() < 40: reasons.append("blurry")
    if prev_gray is not None and prev_gap_s >= 20 and \
       np.abs(g.astype(np.int16) - prev_gray.astype(np.int16)).mean() < 0.5:
        reasons.append("frozen")
    if ref_gray is not None and ref_gray.shape == g.shape:
        (dx, dy), _ = cv2.phaseCorrelate(np.float32(ref_gray), np.float32(g))
        if abs(dx) > 0.05 * g.shape[1] or abs(dy) > 0.05 * g.shape[0]: reasons.append("moved")
    return {"ok": not reasons, "reasons": reasons, "gray": g}

def dhash(frame, size=8):
    g = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (size + 1, size))
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return "".join("1" if b else "0" for b in bits)   # compare hamming distance across days for C2

# FILE: app/services/vision/cleanup.py
import cv2, numpy as np

def _poly(norm, w, h):
    return np.array([[x * w, y * h] for x, y in norm], np.float32).reshape(-1, 1, 2)
def _inside(poly, pt): return cv2.pointPolygonTest(poly, pt, False) >= 0
def _ratio(a, b):   # intersection area / area of b
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area_b = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / area_b

def cleanup(dets, shape, roi_norm, zones_norm, presence_rule, staff_to_subtract):
    h, w = shape[:2]
    roi = _poly(roi_norm, w, h) if roi_norm else None
    zones = [_poly(z["polygon"], w, h) for z in (zones_norm or [])]
    people, chairs, objs = [], [], []
    for x1, y1, x2, y2, conf, name in dets:
        pt = ((x1 + x2) / 2, y2) if name == "person" else ((x1 + x2) / 2, (y1 + y2) / 2)
        if roi is not None and not _inside(roi, pt): continue
        (people if name == "person" else chairs if name == "chair" else objs).append((x1, y1, x2, y2, conf, name))
    counted = []
    for p in people:
        lower = (p[0], p[1] + 0.4 * (p[3] - p[1]), p[2], p[3])
        foot = ((p[0] + p[2]) / 2, p[3])
        if presence_rule == "any": ok = True
        elif presence_rule == "seated": ok = any(_ratio(lower, c) > 0.25 for c in chairs)
        else:  # workstation
            ok = any(_inside(z, foot) for z in zones) or any(_ratio(p, o) > 0.2 for o in objs)
        if ok: counted.append(p)
    return {"people_boxes": [p[:4] for p in people], "counted_boxes": [p[:4] for p in counted],
            "people_count": max(0, len(counted) - staff_to_subtract),
            "equipment": [o for o in objs] + ([c for c in chairs]), "all_people": people}

# FILE: app/services/vision/equipment.py
import cv2, numpy as np

def operability(frame, box, rule, persons=()):
    x1, y1, x2, y2 = map(int, box[:4])
    roi = frame[max(0, y1):y2, max(0, x1):x2]
    if roi.size == 0: return "unknown"
    if rule == "screen_on":
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        return "present_operable" if (g.mean() > 70 or g.std() > 35) else "present_non_operable"
    if rule == "in_use":
        used = any(min(box[2], p[2]) > max(box[0], p[0]) and min(box[3], p[3]) > max(box[1], p[1]) for p in persons)
        return "present_operable" if used else "present_idle"
    return "present_operable"
# NOTE: "in_use" is aggregated over the SESSION: a machine is non-idle if used in >=1 frame.

# FILE: app/services/vision/privacy.py
import cv2, numpy as np

def blurred_thumbnail(frame, person_boxes, annotate_boxes=(), width=640) -> bytes:
    out = frame.copy()
    H, W = out.shape[:2]
    for x1, y1, x2, y2 in person_boxes:
        x1, y1, x2, y2 = max(0, int(x1)), max(0, int(y1)), min(W, int(x2)), min(H, int(y2))
        hy2 = y1 + int(0.28 * (y2 - y1))
        roi = out[y1:hy2, x1:x2]
        if roi.size:
            k = max(9, ((x2 - x1) // 5) | 1)
            out[y1:hy2, x1:x2] = cv2.GaussianBlur(roi, (k, k), 0)
    for x1, y1, x2, y2 in annotate_boxes:
        cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), (79, 70, 229), 2)
    scale = width / out.shape[1]
    out = cv2.resize(out, (width, int(out.shape[0] * scale)))
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return buf.tobytes()      # raw frame is never saved; caller drops it

# FILE: app/services/consolidate.py
import statistics
from collections import Counter

def consolidate(counts: list[int], tol: int = 1, agree_ratio: float = 0.6, min_frames: int = 5):
    """Return dict(consistent, observed, ci_low, ci_high, n)."""
    n = len(counts)
    if n < min_frames:
        return {"consistent": False, "observed": None, "ci_low": None, "ci_high": None, "n": n}
    freq = Counter(counts).most_common()
    top = freq[0][1]
    tied = [v for v, c in freq if c == top]
    med = statistics.median(counts)
    mode = min(tied, key=lambda v: abs(v - med))           # tie -> closest to median
    agree = [c for c in counts if abs(c - mode) <= tol]
    consistent = len(agree) / n >= agree_ratio
    return {"consistent": consistent, "observed": mode if consistent else int(round(med)),
            "ci_low": min(agree) if consistent else min(counts),
            "ci_high": max(agree) if consistent else max(counts), "n": n}

# FILE: app/services/pipeline.py  (hourly analysis loop)
import time
from app.services.consolidate import consolidate
from app.services.vision.quality import quality_gate
from app.services.vision.cleanup import cleanup
from app.services.vision.privacy import blurred_thumbnail

def analyse_hour(grab_frame, detector, tpl, cam, staff_subtract, ref_gray=None, sleep=0):
    counts, eq_counts, tamper, thumbs = [], {}, [], []
    taken, attempts, target = 0, 0, tpl["base_frames"]
    prev_gray, last_t = None, 0
    while True:
        while taken < target and attempts < tpl["max_frames"] * 3:
            attempts += 1
            frame = grab_frame()
            if frame is None: tamper.append("no_frame"); continue
            q = quality_gate(frame, prev_gray, time.time() - last_t if last_t else 0, ref_gray)
            prev_gray, last_t = q["gray"], time.time()
            if not q["ok"]:
                tamper.extend(q["reasons"]); continue          # retake; never count a bad frame
            dets = detector.detect(frame)
            c = cleanup(dets, frame.shape, cam["roi_polygon"], cam["zones"], tpl["presence_rule"], staff_subtract)
            counts.append(c["people_count"])
            for e in c["equipment"]:
                eq_counts.setdefault(e[5], []).append(0)
            per_cls = {}
            for e in c["equipment"]: per_cls[e[5]] = per_cls.get(e[5], 0) + 1
            for k in eq_counts: eq_counts[k][-1] = per_cls.get(k, 0)
            if len(thumbs) < 3:
                thumbs.append(blurred_thumbnail(frame, c["all_people"] and [p[:4] for p in c["all_people"]], c["counted_boxes"]))
            taken += 1
            if sleep: time.sleep(sleep)
        res = consolidate(counts, tpl["count_tolerance"], float(tpl["agree_ratio"]))
        if res["consistent"] or taken >= tpl["max_frames"] or attempts >= tpl["max_frames"] * 3:
            break
        target = min(taken + tpl["step_frames"], tpl["max_frames"])
    state = "ok" if res["consistent"] else "uncertain"
    equipment = {k: consolidate(v, tpl["count_tolerance"], float(tpl["agree_ratio"]), min_frames=3) for k, v in eq_counts.items()}
    return {"observed": res["observed"], "ci_low": res["ci_low"], "ci_high": res["ci_high"], "state": state,
            "frames_used": taken, "per_frame": counts, "equipment": equipment,
            "tamper": sorted(set(tamper)), "thumbs": thumbs}
# NOTE: if frames_used == 0 or tamper dominates -> raise C1, DO NOT evaluate A1.

# FILE: app/services/flag_engine.py
import math
SEV = ["low", "medium", "high", "critical"]

def margin(claimed, tol): return max(tol, math.ceil(0.10 * claimed))
def severity(gap_ratio, repeats):
    base = 0 if gap_ratio < .15 else 1 if gap_ratio < .30 else 2 if gap_ratio < .50 else 3
    return SEV[min(3, base + (1 if repeats >= 3 else 0))]

def evaluate(ctx: dict) -> list[dict]:
    """ctx: claimed, enrolled, sanctioned_max, tol, obs{observed,ci_low,ci_high,state,equipment},
       equipment_rules[{class,label,sanctioned,declared}], repairs{class:qty}, repeats{type:int},
       trainer_verified(bool), camera_tamper(list)"""
    flags, o, c = [], ctx["obs"], ctx["claimed"]
    if ctx["camera_tamper"] and o["observed"] is None:
        return [{"type": "C1", "severity": "high", "state": "flagged",
                 "reason": f"Camera quality problem: {', '.join(ctx['camera_tamper'])}. Counts not evaluated."}]
    if not ctx["trainer_verified"]:
        flags.append({"type": "S1", "severity": "medium", "state": "flagged",
                      "reason": "No face-verified trainer for this session."})
    if o["state"] == "uncertain":
        flags.append({"type": "U1", "severity": "low", "state": "uncertain",
                      "reason": f"Frames disagree ({o['ci_low']}..{o['ci_high']}) even after max frames. Needs review."})
    elif c is not None:
        m = margin(c, ctx["tol"])
        if c > 0 and o["observed"] == 0:
            flags.append({"type": "A4", "severity": "critical", "state": "flagged",
                          "reason": f"Session claimed {c} present but camera saw nobody."})
        elif c > o["ci_high"] + m:
            gap = (c - o["observed"]) / max(c, 1)
            flags.append({"type": "A1", "severity": severity(gap, ctx["repeats"].get("A1", 0)), "state": "flagged",
                          "reason": f"Claimed {c}, camera observed {o['observed']} (range {o['ci_low']}-{o['ci_high']}, margin {m})."})
        elif o["observed"] > c + m:
            flags.append({"type": "A5", "severity": "low", "state": "flagged",
                          "reason": f"Camera saw {o['observed']} but only {c} claimed."})
        if c > ctx["enrolled"]:
            flags.append({"type": "A3", "severity": "medium", "state": "flagged",
                          "reason": f"Claimed {c} but only {ctx['enrolled']} enrolled."})
    if o["state"] == "ok" and o["ci_low"] is not None and o["ci_low"] > ctx["sanctioned_max"]:
        flags.append({"type": "A2", "severity": "high", "state": "flagged",
                      "reason": f"Observed {o['observed']} exceeds sanctioned maximum {ctx['sanctioned_max']}."})
    for r in ctx["equipment_rules"]:
        expected = r["sanctioned"] - ctx["repairs"].get(r["class"], 0)
        e = o["equipment"].get(r["class"])
        if r["declared"] != r["sanctioned"]:
            flags.append({"type": "E3", "severity": "low", "state": "flagged",
                          "reason": f"{r['label']}: declared {r['declared']} vs sanctioned {r['sanctioned']}."})
        if e and e["consistent"] and e["ci_high"] < expected:
            gap = (expected - e["observed"]) / max(expected, 1)
            flags.append({"type": "E1", "severity": severity(gap, ctx["repeats"].get("E1", 0)), "state": "flagged",
                          "reason": f"{r['label']}: sanctioned {expected}, camera saw {e['observed']}."})
        elif e and not e["consistent"]:
            flags.append({"type": "U1", "severity": "low", "state": "uncertain",
                          "reason": f"{r['label']} count unstable across frames."})
    return flags

# FILE: app/services/ledger.py   (signed hash chain in local SQLite)
import hashlib, json, sqlite3, threading
from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from app.core.config import settings

_lock = threading.Lock()
_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(settings.edge_signing_key_hex))
PUBLIC_KEY_HEX = _key.public_key().public_bytes_raw().hex()
GENESIS = "0" * 64

def conn():
    c = sqlite3.connect(settings.local_db_path, check_same_thread=False)
    c.execute("""CREATE TABLE IF NOT EXISTS ledger(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT,
        payload TEXT, prev_hash TEXT, hash TEXT UNIQUE, sig TEXT, ts TEXT, synced INTEGER DEFAULT 0)""")
    return c

def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()

def append(kind: str, payload: dict) -> dict:
    with _lock:
        c = conn()
        row = c.execute("SELECT hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        ts = datetime.now(timezone.utc).isoformat()
        h = hashlib.sha256(canonical({"kind": kind, "payload": payload, "prev": prev, "ts": ts})).hexdigest()
        sig = _key.sign(h.encode()).hex()
        cur = c.execute("INSERT INTO ledger(kind,payload,prev_hash,hash,sig,ts) VALUES(?,?,?,?,?,?)",
                        (kind, json.dumps(payload, default=str), prev, h, sig, ts))
        c.commit()
        return {"seq": cur.lastrowid, "hash": h}

def verify_chain(rows: list[dict], public_key_hex: str):
    """rows ordered by seq: dict(kind,payload(dict),prev_hash,hash,sig,ts). Returns (ok, bad_index)."""
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
    prev = GENESIS
    for i, r in enumerate(rows):
        h = hashlib.sha256(canonical({"kind": r["kind"], "payload": r["payload"], "prev": prev, "ts": r["ts"]})).hexdigest()
        try: pub.verify(bytes.fromhex(r["sig"]), h.encode())
        except Exception: return False, i
        if h != r["hash"] or r["prev_hash"] != prev: return False, i
        prev = h
    return True, None
# Central side: on sync, register edge_devices.public_key_hex once, then run verify_chain
# on a device's rows ordered by seq; if invalid raise D1 flag.

# FILE: app/services/sync.py   (store-and-forward, idempotent, flags first)
import json, os
from app.core.config import settings
from app.db.supabase_client import sb
from app.services.ledger import conn

STATE = {"offline": settings.force_offline}

def unsynced_count():
    return conn().execute("SELECT COUNT(*) FROM ledger WHERE synced=0").fetchone()[0]

def sync_tick(batch=25):
    if STATE["offline"]: return {"skipped": "offline"}
    c = conn()
    rows = c.execute("""SELECT seq,kind,payload,prev_hash,hash,sig,ts FROM ledger WHERE synced=0
        ORDER BY CASE kind WHEN 'flag' THEN 0 ELSE 1 END, seq LIMIT ?""", (batch,)).fetchall()
    done = 0
    for seq, kind, payload, prev, h, sig, ts in rows:
        p = json.loads(payload)
        try:
            for i, local in enumerate(p.pop("evidence_local", [])):       # upload blurred thumbs
                path = f"{p['centre_id']}/{h[:12]}_{i}.jpg"
                with open(local, "rb") as f:
                    sb.storage.from_("evidence").upload(path, f.read(), {"content-type": "image/jpeg", "upsert": "true"})
                p.setdefault("evidence_paths", []).append(path)
                os.remove(local)
            sb.table("ledger_entries").upsert({"device_id": settings.device_id, "seq": seq, "kind": kind,
                "payload": p, "prev_hash": prev, "hash": h, "sig": sig, "ts": ts}, on_conflict="hash").execute()
            if kind == "observation":
                sb.table("observations").upsert({**p["row"], "ledger_hash": h}, on_conflict="ledger_hash").execute()
            elif kind == "flag":
                sb.table("flags").upsert({**p["row"], "evidence_paths": p.get("evidence_paths", []),
                                          "ledger_hash": h}, on_conflict="ledger_hash").execute()
            c.execute("UPDATE ledger SET synced=1 WHERE seq=?", (seq,)); c.commit(); done += 1
        except Exception:
            break      # network down or error: stop, retry next tick (nothing lost)
    return {"synced": done, "pending": unsynced_count()}

# FILE: app/services/face.py   (STAFF ONLY, 1:1 verification)
import numpy as np, cv2, base64
from cryptography.fernet import Fernet
from insightface.app import FaceAnalysis
from app.core.config import settings

_fernet = Fernet(settings.fernet_key.encode())
_app = FaceAnalysis(name="buffalo_s", providers=["CPUExecutionProvider"])
_app.prepare(ctx_id=-1, det_size=(320, 320))
THRESHOLD = 0.45     # tune on your own test images

def decode(image_b64: str):
    data = base64.b64decode(image_b64.split(",")[-1])
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)

def embed(img):
    faces = _app.get(img)
    if len(faces) != 1: return None           # reject 0 or multiple faces
    return faces[0].normed_embedding.astype(np.float32)

def encrypt(e: np.ndarray) -> bytes: return _fernet.encrypt(e.tobytes())
def decrypt(b: bytes) -> np.ndarray: return np.frombuffer(_fernet.decrypt(b), dtype=np.float32)

def verify(img, stored_enc: bytes):
    e = embed(img)
    if e is None: return False, 0.0
    score = float(np.dot(e, decrypt(stored_enc)))
    return score >= THRESHOLD, score          # image is discarded by the caller; never stored
# Route rules: enrol only if staff.face_consent==true; verify against the LOGGED-IN
# trainer's own embedding only (never search across people); allow scan only within
# [session.start-15min, session.end]; delete embedding when staff is deactivated.
# Basic liveness (should-have): require 3 frames over ~2 s with small head movement.

# FILE: app/routers/attendance.py  (claim = trainer's roster count)
from fastapi import APIRouter, Depends, HTTPException
from app.core.deps import require
from app.db.supabase_client import sb
from app.services import ledger
router = APIRouter()

@router.post("/my/sessions/{sid}/claim")
def claim(sid: str, body: dict, user=Depends(require("trainer", "centre_admin"))):
    s = sb.table("sessions").select("*").eq("id", sid).single().execute().data
    if str(s["centre_id"]) != str(user["centre_id"]): raise HTTPException(403, detail={"detail": "Wrong centre", "code": "WRONG_CENTRE"})
    ids = body["present_trainee_ids"]
    existing = sb.table("attendance_claims").select("id").eq("session_id", sid).execute().data
    if existing and not body.get("edit_reason"):
        raise HTTPException(409, detail={"detail": "Already submitted. Provide edit_reason.", "code": "CLAIM_LOCKED"})
    row = {"session_id": sid, "centre_id": s["centre_id"], "claimed_count": len(ids),
           "present_trainee_ids": ids, "marked_by": user["id"], "edit_reason": body.get("edit_reason")}
    sb.table("attendance_claims").upsert(row, on_conflict="session_id").execute()
    ledger.append("claim", {"centre_id": s["centre_id"], "session_id": sid, "claimed": len(ids)})
    return {"claimed_count": len(ids)}

# FILE: app/services/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from app.services.sync import sync_tick
sched = BackgroundScheduler(timezone="UTC")
def start():
    sched.add_job(sync_tick, "interval", seconds=20, max_instances=1, id="sync")
    # sched.add_job(capture_tick, "interval", seconds=30, max_instances=1)  # live cameras:
    #   for each LIVE centre with active session: if now >= next planned random time -> analyse
    #   planned times = random.sample(range(window_seconds), k) generated when session opens
    sched.start()

# FILE: app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.services import scheduler
from app.routers import applications, trainees, attendance   # + all other routers

app = FastAPI(title="Dev_Skill API")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.allowed_origins.split(",")],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
for r in (applications.router, trainees.router, attendance.router):
    app.include_router(r)

@app.on_event("startup")
def _start(): scheduler.start()

@app.get("/health")
def health(): return {"ok": True}

# FILE: app/routers/edge.py  (Demo Lab core: simulate a session from a sample video)
# POST /edge/demo/run {camera_id, session_id, video, scenario}
#  1 load tpl + camera + staff_subtract from staff_attendance
#  2 grab_frame = lambda: seek random frame index in sample_videos/<video> (cv2.VideoCapture,
#    CAP_PROP_POS_FRAMES) and read it
#  3 res = analyse_hour(grab_frame, Detector(tpl.detector_classes, tpl.min_confidence), tpl, cam, subtract)
#  4 save thumbs to data/evidence/*.jpg ; ledger.append("observation", {"row": {...}, "centre_id":..,
#    "evidence_local":[paths]})
#  5 flags = evaluate(ctx) ; for each: ledger.append("flag", {"row": {...}, "centre_id":.., "evidence_local":[...]})
#  6 call sync_tick() if online ; return summary {observed, claimed, flags[]}
# Scenarios just choose a different video (normal, few_people, equipment_removed, dark, frozen).

# FILE: eval/evaluate.py  (stated deliverable)
# For each labelled frame set in eval/labels.csv (frame_path, true_people, true_<class>, condition):
#   run detector+cleanup, compute MAE, within-±1 accuracy, per-class precision/recall,
#   then simulate claims (true, +20%, -20%) to compute FLAG-level FP/FN per condition
#   (bright, dim, occluded, far camera, low-res), plus UNCERTAIN rate, latency, model size,
#   baseline vs fine-tuned vs quantized. Write eval/results.json (read by GET /accuracy).