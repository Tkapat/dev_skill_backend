----------------mongo.py----------------
# FILE: app/db/mongo.py
import secrets, time, hmac, hashlib
from datetime import datetime
from bson import ObjectId, Binary
from fastapi import HTTPException
from pymongo import MongoClient, ReturnDocument
from app.core.config import settings

client = MongoClient(settings.mongodb_uri, tz_aware=True,
                     serverSelectionTimeoutMS=8000, maxPoolSize=20)
db = client[settings.mongodb_db]

def oid(v) -> ObjectId:
    try:
        return ObjectId(str(v))
    except Exception:
        raise HTTPException(422, detail={"detail": "Invalid id", "code": "BAD_ID"})

def ser(x):
    """Mongo doc -> JSON-safe. _id -> id, ObjectId -> str, datetime -> ISO, bytes dropped."""
    if isinstance(x, list):
        return [ser(i) for i in x]
    if isinstance(x, dict):
        return {("id" if k == "_id" else k): ser(v) for k, v in x.items()}
    if isinstance(x, ObjectId):
        return str(x)
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, (bytes, Binary)):
        return None
    return x

def next_seq(name: str) -> int:
    d = db.counters.find_one_and_update({"_id": name}, {"$inc": {"n": 1}},
                                        upsert=True, return_document=ReturnDocument.AFTER)
    return d["n"]

def next_centre_code() -> str:
    return f"DSK-WB-{next_seq('centre'):04d}"

_ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
def gen_tracking_code() -> str:
    return "APP-" + "".join(secrets.choice(_ALPHA) for _ in range(8))

# ---- signed file URLs (evidence + documents) ----
def sign_file_url(kind: str, fid: str, ttl: int = 60) -> str:
    exp = int(time.time()) + ttl
    sig = hmac.new(settings.file_url_secret.encode(), f"{kind}:{fid}:{exp}".encode(),
                   hashlib.sha256).hexdigest()
    return f"/files/{kind}/{fid}?exp={exp}&sig={sig}"      # frontend prepends API URL

def verify_file_sig(kind: str, fid: str, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    good = hmac.new(settings.file_url_secret.encode(), f"{kind}:{fid}:{exp}".encode(),
                    hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig)

#----------------init_db.py------------------
# FILE: scripts/init_db.py      run once:  python -m scripts.init_db
from datetime import datetime, timezone
from getpass import getpass
from pymongo import ASCENDING as A, DESCENDING as D
from app.db.mongo import db
from app.core.security import hash_pw, password_ok

def S(*enum_pairs, required=(), **extra):
    props = {k: {"enum": list(v)} for k, v in enum_pairs}
    props.update(extra)
    return {"bsonType": "object", "required": list(required), "properties": props}

VALIDATORS = {
  "users": S(("role", ["super_admin","scheme_officer","auditor","centre_admin","trainer"]),
             required=["login_id","password_hash","role","full_name","active"]),
  "centres": S(("status", ["pending_setup","awaiting_camera_approval","live","suspended","terminated"]),
               required=["code","name","max_trainees","status"]),
  "flags": S(("type", ["A1","A2","A3","A4","A5","E1","E2","E3","S1","C1","C2","C3","D1","U1","P1"]),
             ("severity", ["low","medium","high","critical"]),
             ("state", ["flagged","uncertain"]),
             ("status", ["open","acknowledged","centre_responded","under_review","resolved","dismissed","escalated"]),
             required=["centre_id","type","severity","state","status","reason"]),
  "applications": S(("status", ["submitted","under_review","query_raised","resubmitted","approved","rejected"]),
                    required=["tracking_code","centre_name","max_trainees","status"]),
}
PLAIN = ["refresh_tokens","schemes","trade_templates","staff","staff_faces","batches","holidays",
         "trainees","sessions","attendance_claims","staff_attendance","cameras","camera_health_events",
         "frame_fingerprints","edge_devices","ledger_entries","observations","evidence_blobs",
         "repair_declarations","change_requests","notices","audit_log","counters"]

def ensure(name, validator=None):
    opts = {"validator": {"$jsonSchema": validator}, "validationLevel": "strict",
            "validationAction": "error"} if validator else {}
    if name not in db.list_collection_names():
        db.create_collection(name, **opts)
    elif validator:
        db.command("collMod", name, **opts)

for n, v in VALIDATORS.items(): ensure(n, v)
for n in PLAIN: ensure(n)

def ix(c, keys, **kw): db[c].create_index(keys, **kw)
ix("users", [("login_id", A)], unique=True);          ix("users", [("centre_id", A)])
ix("refresh_tokens", [("token_hash", A)], unique=True)
ix("refresh_tokens", [("expires_at", A)], expireAfterSeconds=0)          # auto-cleanup
ix("schemes", [("code", A)], unique=True)
ix("trade_templates", [("scheme_id", A), ("trade_name", A)], unique=True)
ix("applications", [("tracking_code", A)], unique=True)
ix("applications", [("status", A), ("submitted_at", D)]);  ix("applications", [("contact_phone", A)])
ix("centres", [("code", A)], unique=True);  ix("centres", [("status", A)]);  ix("centres", [("district", A)])
ix("staff", [("centre_id", A)]);  ix("staff", [("user_id", A)], unique=True, sparse=True)
ix("batches", [("centre_id", A)])
ix("holidays", [("centre_id", A), ("holiday_date", A)], unique=True)
ix("trainees", [("centre_id", A), ("active", A)])
ix("sessions", [("batch_id", A), ("session_date", A)], unique=True)
ix("sessions", [("centre_id", A), ("session_date", D)])
ix("attendance_claims", [("session_id", A)], unique=True)
ix("staff_attendance", [("session_id", A), ("staff_id", A)], unique=True)
ix("cameras", [("centre_id", A)])
ix("camera_health_events", [("camera_id", A), ("ts", D)])
ix("camera_health_events", [("ts", A)], expireAfterSeconds=90*86400)
ix("frame_fingerprints", [("camera_id", A), ("dhash", A)])
ix("frame_fingerprints", [("captured_at", A)], expireAfterSeconds=60*86400)
ix("ledger_entries", [("hash", A)], unique=True);  ix("ledger_entries", [("device_id", A), ("seq", A)], unique=True)
ix("observations", [("ledger_hash", A)], unique=True, sparse=True)
ix("observations", [("session_id", A)]);  ix("observations", [("centre_id", A), ("hour_bucket", D)])
ix("evidence_blobs", [("centre_id", A), ("created_at", D)])
ix("evidence_blobs", [("created_at", A)], expireAfterSeconds=90*86400)   # retention limit (privacy)
ix("flags", [("ledger_hash", A)], unique=True, sparse=True)
ix("flags", [("centre_id", A), ("created_at", D)]);  ix("flags", [("status", A), ("severity", A), ("created_at", D)])
ix("flags", [("type", A)])
ix("notices", [("centre_id", A), ("created_at", D)])
ix("change_requests", [("status", A), ("created_at", D)])
ix("repair_declarations", [("status", A)])
ix("audit_log", [("ts", D)]);  ix("audit_log", [("actor", A)])

# ---- seed: scheme + two demo templates ----
if not db.schemes.find_one({"code": "DEMO-SDS"}):
    sid = db.schemes.insert_one({"name": "Demo Skill Development Scheme", "code": "DEMO-SDS",
        "description": "Seed scheme for the SIH demonstration", "active": True,
        "created_at": datetime.now(timezone.utc)}).inserted_id
    base = {"scheme_id": sid, "count_tolerance": 1, "agree_ratio": 0.6, "min_confidence": 0.35,
            "base_frames": 10, "step_frames": 5, "max_frames": 40, "active": True}
    db.trade_templates.insert_many([
      {**base, "trade_name": "Sewing and Tailoring", "presence_rule": "seated",
       "detector_classes": ["person","chair","sewing machine"],
       "equipment_rules": [
         {"class":"sewing machine","label":"Sewing machine","min_units_per_trainee":0.5,"operability":"in_use","required":True},
         {"class":"chair","label":"Chair","min_units_per_trainee":1.0,"operability":"none","required":True}]},
      {**base, "trade_name": "Computer Lab (Basic IT)", "presence_rule": "seated",
       "detector_classes": ["person","chair","laptop","tv"],
       "equipment_rules": [
         {"class":"tv","label":"Monitor","min_units_per_trainee":1.0,"operability":"screen_on","required":True},
         {"class":"chair","label":"Chair","min_units_per_trainee":1.0,"operability":"none","required":True}]}])

# ---- first super admin (interactive) ----
if not db.users.find_one({"role": "super_admin"}):
    email = input("Super admin email: ").strip().lower()
    pw = getpass("Password (min 10, upper, lower, digit, special): ")
    assert password_ok(pw), "Password too weak"
    db.users.insert_one({"login_id": email, "password_hash": hash_pw(pw), "role": "super_admin",
        "centre_id": None, "full_name": "System Admin", "must_change_password": False,
        "temp_password_expires_at": None, "active": True, "token_version": 0,
        "failed_attempts": 0, "locked_until": None, "mfa_enabled": False,
        "created_at": datetime.now(timezone.utc)})
print("DB ready.")

#-----------------security-------------------
# FILE: app/core/security.py
import re, secrets, hashlib, jwt
from datetime import datetime, timedelta, timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from app.core.config import settings

_ph = PasswordHasher()
ACCESS_MIN, REFRESH_DAYS, MFA_MIN = 30, 7, 5
_DUMMY = _ph.hash("dummy-password-for-timing")

def hash_pw(p: str) -> str: return _ph.hash(p)
def check_pw(h: str | None, p: str) -> bool:
    try:
        return _ph.verify(h or _DUMMY, p) and h is not None
    except (VerifyMismatchError, InvalidHashError):
        return False

def password_ok(p: str) -> bool:
    return (len(p) >= 10 and re.search(r"[a-z]", p) and re.search(r"[A-Z]", p)
            and re.search(r"\d", p) and re.search(r"[^\w\s]", p)) is not None

def _tok(payload: dict, minutes: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({**payload, "iat": now, "exp": now + timedelta(minutes=minutes)},
                      settings.jwt_secret, algorithm="HS256")

def make_access(u: dict) -> str:
    return _tok({"sub": str(u["_id"]), "tv": u.get("token_version", 0), "typ": "access"}, ACCESS_MIN)
def make_mfa_token(u: dict) -> str:
    return _tok({"sub": str(u["_id"]), "typ": "mfa"}, MFA_MIN)
def decode(token: str, typ: str) -> dict:
    d = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])   # raises on bad/expired
    if d.get("typ") != typ: raise jwt.InvalidTokenError("wrong type")
    return d

def new_refresh() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, hashlib.sha256(raw.encode()).hexdigest()
def hash_refresh(raw: str) -> str: return hashlib.sha256(raw.encode()).hexdigest()

#-----------------deps.py---------------------
# FILE: app/core/deps.py   (replaces the Supabase version)
from datetime import datetime, timezone
import jwt
from cachetools import TTLCache
from fastapi import Depends, Header, HTTPException
from app.db.mongo import db, oid
from app.core.security import decode

_cache = TTLCache(maxsize=512, ttl=30)
CENTRE_ROLES = {"centre_admin", "trainer"}
GOVT_ROLES = {"super_admin", "scheme_officer", "auditor"}

def invalidate(uid=None):
    _cache.pop(str(uid), None) if uid else _cache.clear()

def _err(status, code, msg): raise HTTPException(status, detail={"detail": msg, "code": code})

def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "): _err(401, "NO_TOKEN", "Missing token")
    try:
        claims = decode(authorization[7:], "access")
    except jwt.ExpiredSignatureError:
        _err(401, "TOKEN_EXPIRED", "Session expired")
    except Exception:
        _err(401, "BAD_TOKEN", "Invalid token")
    uid = claims["sub"]
    u = _cache.get(uid)
    if u is None:
        u = db.users.find_one({"_id": oid(uid)}, {"password_hash": 0, "mfa_secret_enc": 0})
        if u: _cache[uid] = u
    if not u or not u.get("active"): _err(403, "DISABLED", "Account disabled")
    if u.get("token_version", 0) != claims.get("tv"): _err(401, "BAD_TOKEN", "Session revoked")
    return {**u, "id": str(u["_id"])}            # centre_id stays an ObjectId

def require(*roles, allow_unchanged_password=False):
    def dep(user=Depends(current_user)):
        if user["role"] not in roles: _err(403, "FORBIDDEN", "Forbidden")
        if user.get("must_change_password") and not allow_unchanged_password:
            exp = user.get("temp_password_expires_at")
            if exp and exp < datetime.now(timezone.utc):
                _err(403, "CREDENTIALS_EXPIRED", "Credentials expired")
            _err(403, "PASSWORD_CHANGE_REQUIRED", "Change password first")
        return user
    return dep

def assert_centre_access(user, centre_id):
    if user["role"] in CENTRE_ROLES and str(user.get("centre_id")) != str(centre_id):
        _err(403, "WRONG_CENTRE", "Not your centre")

# FILE: app/routers/auth.py   (public: /auth/*  ; protected: /me*)
from datetime import datetime, timedelta, timezone
import pyotp
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException
from app.core.config import settings
from app.core.deps import require, current_user, invalidate, GOVT_ROLES
from app.core.security import *
from app.db.mongo import db, oid, ser

router = APIRouter()
_f = Fernet(settings.fernet_key.encode())
MAX_FAILS, LOCK_MIN = 5, 15

def _bad(status, code, msg): raise HTTPException(status, detail={"detail": msg, "code": code})
def _now(): return datetime.now(timezone.utc)

def _public(u): 
    return ser({k: u.get(k) for k in ("_id","login_id","role","centre_id","full_name",
                "must_change_password","mfa_enabled")})

def _issue(u) -> dict:
    raw, h = new_refresh()
    db.refresh_tokens.insert_one({"user_id": u["_id"], "token_hash": h,
        "expires_at": _now() + timedelta(days=REFRESH_DAYS), "created_at": _now()})
    return {"access_token": make_access(u), "refresh_token": raw, "user": _public(u)}

@router.post("/auth/login")                       # add slowapi limit: 10/min per IP
def login(body: dict):
    login_id = str(body.get("login_id", "")).strip().lower()
    u = db.users.find_one({"login_id": login_id})
    if u and u.get("locked_until") and u["locked_until"] > _now():
        _bad(429, "ACCOUNT_LOCKED", "Too many attempts. Try again in a few minutes.")
    if not (u and u["active"] and check_pw(u["password_hash"], str(body.get("password", "")))):
        if u:
            fails = u.get("failed_attempts", 0) + 1
            upd = {"failed_attempts": fails}
            if fails >= MAX_FAILS:
                upd = {"failed_attempts": 0, "locked_until": _now() + timedelta(minutes=LOCK_MIN)}
            db.users.update_one({"_id": u["_id"]}, {"$set": upd})
        _bad(401, "INVALID_CREDENTIALS", "Incorrect ID or password")        # same message for both cases
    db.users.update_one({"_id": u["_id"]}, {"$set": {"failed_attempts": 0, "locked_until": None,
                                                      "last_login_at": _now()}})
    if u.get("mfa_enabled") and u["role"] in GOVT_ROLES:
        return {"mfa_required": True, "mfa_token": make_mfa_token(u)}
    return _issue(u)

@router.post("/auth/mfa/login")
def mfa_login(body: dict):
    try: claims = decode(body["mfa_token"], "mfa")
    except Exception: _bad(401, "BAD_TOKEN", "MFA step expired. Log in again.")
    u = db.users.find_one({"_id": oid(claims["sub"])})
    secret = _f.decrypt(u["mfa_secret_enc"]).decode()
    if not pyotp.TOTP(secret).verify(str(body.get("code", "")), valid_window=1):
        _bad(401, "BAD_MFA", "Incorrect code")
    return _issue(u)

@router.post("/auth/refresh")
def refresh(body: dict):
    h = hash_refresh(str(body.get("refresh_token", "")))
    t = db.refresh_tokens.find_one_and_delete({"token_hash": h})            # rotation: one-time use
    if not t or t["expires_at"] < _now(): _bad(401, "BAD_TOKEN", "Please log in again")
    u = db.users.find_one({"_id": t["user_id"]})
    if not u or not u["active"]: _bad(401, "BAD_TOKEN", "Please log in again")
    return _issue(u)

@router.post("/auth/logout")
def logout(body: dict):
    db.refresh_tokens.delete_one({"token_hash": hash_refresh(str(body.get("refresh_token", "")))})
    return {"ok": True}

@router.get("/me")
def me(user=Depends(require(*["super_admin","scheme_officer","auditor","centre_admin","trainer"],
                            allow_unchanged_password=True))):
    centre = db.centres.find_one({"_id": user["centre_id"]}, {"code":1,"name":1,"status":1}) if user.get("centre_id") else None
    return {**_public(user), "centre": ser(centre)}

@router.post("/me/change-password")
def change_password(body: dict, user=Depends(require(*["super_admin","scheme_officer","auditor",
                    "centre_admin","trainer"], allow_unchanged_password=True))):
    full = db.users.find_one({"_id": user["_id"]})
    if not check_pw(full["password_hash"], str(body.get("current_password", ""))):
        _bad(401, "INVALID_CREDENTIALS", "Current password is incorrect")
    new = str(body.get("new_password", ""))
    if not password_ok(new): _bad(422, "WEAK_PASSWORD", "Use 10+ characters with upper, lower, digit and special.")
    if check_pw(full["password_hash"], new): _bad(422, "SAME_PASSWORD", "Choose a different password")
    db.users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": hash_pw(new),
        "must_change_password": False, "temp_password_expires_at": None},
        "$inc": {"token_version": 1}})                                       # kills every old token
    db.refresh_tokens.delete_many({"user_id": user["_id"]})
    invalidate(user["id"])
    return _issue(db.users.find_one({"_id": user["_id"]}))                   # frontend stays logged in

@router.post("/me/mfa/setup")                                               # govt roles only
def mfa_setup(user=Depends(require("super_admin","scheme_officer","auditor"))):
    secret = pyotp.random_base32()
    db.users.update_one({"_id": user["_id"]}, {"$set": {"mfa_pending_enc": _f.encrypt(secret.encode())}})
    return {"otpauth_uri": pyotp.TOTP(secret).provisioning_uri(name=user["login_id"], issuer_name="Dev_Skill Gov"),
            "secret": secret}
@router.post("/me/mfa/enable")
def mfa_enable(body: dict, user=Depends(require("super_admin","scheme_officer","auditor"))):
    full = db.users.find_one({"_id": user["_id"]})
    secret = _f.decrypt(full["mfa_pending_enc"]).decode()
    if not pyotp.TOTP(secret).verify(str(body.get("code", "")), valid_window=1): _bad(401, "BAD_MFA", "Incorrect code")
    db.users.update_one({"_id": user["_id"]}, {"$set": {"mfa_enabled": True, "mfa_secret_enc": full["mfa_pending_enc"]},
                                               "$unset": {"mfa_pending_enc": ""}})
    invalidate(user["id"]); return {"mfa_enabled": True}
# main.py: include this router. Add a global slowapi limiter (public routes 20/min/IP).

# FILE: app/services/credentials.py   (replaces Supabase version)
import secrets, string
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
from app.db.mongo import db
from app.core.security import hash_pw

def gen_password(n: int = 12) -> str:
    sets = [string.ascii_lowercase, string.ascii_uppercase, string.digits, "@#$%&*"]
    pool = "".join(sets)
    while True:
        p = "".join(secrets.choice(pool) for _ in range(n))
        if all(any(c in s for c in p) for s in sets): return p

def create_login(login_id, password, role, centre_id, full_name, phone=None):
    now = datetime.now(timezone.utc)
    try:
        return db.users.insert_one({"login_id": login_id.strip().lower(), "password_hash": hash_pw(password),
            "role": role, "centre_id": centre_id, "full_name": full_name, "phone": phone,
            "must_change_password": True, "temp_password_expires_at": now + timedelta(hours=72),
            "active": True, "token_version": 0, "failed_attempts": 0, "locked_until": None,
            "mfa_enabled": False, "created_at": now}).inserted_id
    except DuplicateKeyError:
        raise HTTPException(409, detail={"detail": "Login already exists", "code": "LOGIN_EXISTS"})
# Trainer login id: f"{centre_code}-T{next_seq('trainer:'+centre_code):02d}"
# Reset credentials: new gen_password(), set password_hash, must_change_password=True,
#   temp_password_expires_at=now+72h, $inc token_version, delete refresh_tokens, invalidate(uid).

# FILE: app/routers/applications.py   (approve flow, atomic and double-click safe)
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from app.core.deps import require
from app.core.audit import audit
from app.db.mongo import db, oid, next_centre_code
from app.services.credentials import gen_password, create_login

router = APIRouter()
GOVT_WRITE = ("super_admin", "scheme_officer")

def create_centre_from_application(a: dict, user, created_by_govt=False):
    """Shared by approve and POST /centres (govt direct). Returns (centre, login_id, password)."""
    now = datetime.now(timezone.utc)
    code = next_centre_code()
    centre = {"code": code, "application_id": a["_id"], "name": a["centre_name"],
        "scheme_id": a["scheme_id"], "trade_template_id": a["trade_template_id"],
        "max_trainees": a["max_trainees"], "enrolled_count": 0,
        "address": a["address"], "district": a["district"], "state": a["state"],
        "lat": a.get("lat"), "lng": a.get("lng"),
        "contact_phone": a["contact_phone"], "contact_email": a.get("contact_email"),
        "equipment": [{"class": e["class"], "label": e.get("label", e["class"]),
                       "sanctioned_qty": e["qty"], "declared_qty": 0} for e in a["equipment"]],
        "status": "pending_setup", "risk_score": 0, "created_by_govt": created_by_govt,
        "created_at": now, "updated_at": now}
    cid = db.centres.insert_one(centre).inserted_id
    try:
        pwd = gen_password()
        create_login(code, pwd, "centre_admin", cid, a["centre_name"] + " Admin", a["contact_phone"])
    except Exception:
        db.centres.delete_one({"_id": cid}); raise
    return {**centre, "_id": cid}, code, pwd

@router.post("/applications/{app_id}/approve")
def approve(app_id: str, user=Depends(require(*GOVT_WRITE))):
    now = datetime.now(timezone.utc)
    prev = db.applications.find_one_and_update(                       # atomic claim: second click gets 409
        {"_id": oid(app_id), "status": {"$in": ["submitted", "under_review", "resubmitted"]}},
        {"$set": {"status": "approved", "reviewed_by": oid(user["id"]), "updated_at": now}})
    if not prev:
        raise HTTPException(409, detail={"detail": "Not approvable", "code": "BAD_STATE"})
    try:
        centre, code, pwd = create_centre_from_application(prev, user)
    except Exception:
        db.applications.update_one({"_id": prev["_id"]}, {"$set": {"status": prev["status"]}})   # compensate
        raise
    db.applications.update_one({"_id": prev["_id"]}, {"$set": {"centre_id": centre["_id"]},
        "$push": {"events": {"actor": oid(user["id"]), "action": "approved", "comment": None, "ts": now}}})
    audit(user, "application.approve", "application", app_id, {"centre_code": code})
    return {"centre_id": str(centre["_id"]), "login_id": code, "temporary_password": pwd,
            "expires_in_hours": 72, "note": "Shown once. Share securely."}
# Every other status change (query/reject/resubmit) must also $push an events entry.

# FILE: app/routers/trainees.py  (race-safe cap WITHOUT triggers)
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from app.core.deps import require
from app.db.mongo import db, oid, ser

router = APIRouter()
def _cap_err(): raise HTTPException(409, detail={"detail": "Sanctioned limit reached. Request extra enrolment.", "code": "ENROLMENT_CAP_REACHED"})

def take_seat(centre_id) -> bool:
    """Atomic: increments only if enrolled_count < max_trainees. Same pattern for reactivation."""
    r = db.centres.update_one({"_id": centre_id, "status": {"$nin": ["suspended", "terminated"]},
                               "$expr": {"$lt": ["$enrolled_count", "$max_trainees"]}},
                              {"$inc": {"enrolled_count": 1}})
    return r.modified_count == 1

@router.post("/my/trainees")
def enrol(body: dict, user=Depends(require("centre_admin"))):
    cid = user["centre_id"]
    if not take_seat(cid):
        c = db.centres.find_one({"_id": cid}, {"status": 1})
        if c["status"] in ("suspended", "terminated"):
            raise HTTPException(403, detail={"detail": "Centre not active", "code": "CENTRE_INACTIVE"})
        _cap_err()
    try:
        doc = {"centre_id": cid, "batch_id": oid(body["batch_id"]) if body.get("batch_id") else None,
               "full_name": body["full_name"].strip(), "external_id": body.get("external_id"),
               "active": True, "created_at": datetime.now(timezone.utc)}      # NO photo/face fields
        doc["_id"] = db.trainees.insert_one(doc).inserted_id
    except Exception:
        db.centres.update_one({"_id": cid}, {"$inc": {"enrolled_count": -1}}); raise   # give the seat back
    return ser(doc)

@router.post("/my/trainees/{tid}/deactivate")
def deactivate(tid: str, user=Depends(require("centre_admin"))):
    r = db.trainees.update_one({"_id": oid(tid), "centre_id": user["centre_id"], "active": True},
                               {"$set": {"active": False}})
    if r.modified_count: db.centres.update_one({"_id": user["centre_id"]}, {"$inc": {"enrolled_count": -1}})
    return {"ok": True}
# Approved 'enrolment' change request: $inc centres.max_trainees by extra. Never touch enrolled_count there.

# FILE: app/services/dbfuncs.py   (replaces SQL functions and views)
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from pymongo import UpdateOne
from app.db.mongo import db

IST = ZoneInfo("Asia/Kolkata")
OPEN = ["open", "acknowledged", "centre_responded", "under_review", "escalated"]
WEIGHT = {"low": 1, "medium": 3, "high": 6, "critical": 10}

def generate_sessions(centre_id, d_from: date, d_to: date) -> int:
    hol = {h["holiday_date"] for h in db.holidays.find({"centre_id": centre_id})}
    ops, d = [], d_from
    while d <= d_to:
        iso = d.isoformat()
        for b in db.batches.find({"centre_id": centre_id, "active": True}):
            if (d.isoweekday() in b["weekdays"] and iso not in hol
                and (not b.get("start_date") or iso >= b["start_date"])
                and (not b.get("end_date") or iso <= b["end_date"])):
                st = datetime.combine(d, datetime.strptime(b["start_time"], "%H:%M").time(), IST)
                en = datetime.combine(d, datetime.strptime(b["end_time"], "%H:%M").time(), IST)
                ops.append(UpdateOne({"batch_id": b["_id"], "session_date": iso},
                    {"$setOnInsert": {"centre_id": centre_id, "start_ts": st.astimezone(timezone.utc),
                     "end_ts": en.astimezone(timezone.utc), "status": "scheduled",
                     "planned_captures": 0, "done_captures": 0}}, upsert=True))
        d += timedelta(days=1)
    return db.sessions.bulk_write(ops).upserted_count if ops else 0
# Call after every batch create/update (today..today+14) and nightly from APScheduler.

def recompute_risk(centre_id) -> float:
    now = datetime.now(timezone.utc); score = 0.0
    for f in db.flags.find({"centre_id": centre_id, "state": "flagged", "status": {"$in": OPEN},
                            "created_at": {"$gte": now - timedelta(days=30)}}, {"severity": 1, "created_at": 1}):
        score += WEIGHT[f["severity"]] * (1.5 if f["created_at"] >= now - timedelta(days=7) else 1)
    score = round(min(100, score), 2)
    db.centres.update_one({"_id": centre_id}, {"$set": {"risk_score": score}})
    return score
# Call after every new flag and every flag status change.

def expire_repairs() -> int:      # daily job
    return db.repair_declarations.update_many(
        {"status": "approved", "approved_until": {"$lt": date.today().isoformat()}},
        {"$set": {"status": "expired"}}).modified_count

def attendance_series(centre_id, d_from: str, d_to: str):
    return list(db.sessions.aggregate([
      {"$match": {"centre_id": centre_id, "session_date": {"$gte": d_from, "$lte": d_to}}},
      {"$lookup": {"from": "attendance_claims", "localField": "_id", "foreignField": "session_id", "as": "claim"}},
      {"$lookup": {"from": "observations", "let": {"sid": "$_id"}, "as": "obs", "pipeline": [
          {"$match": {"$expr": {"$eq": ["$session_id", "$$sid"]}, "observed_count": {"$ne": None}}},
          {"$group": {"_id": None, "observed": {"$avg": "$observed_count"}, "ci_low": {"$min": "$ci_low"},
                      "ci_high": {"$max": "$ci_high"}, "frames": {"$sum": "$frames_used"},
                      "any_uncertain": {"$max": {"$cond": [{"$eq": ["$state", "uncertain"]}, 1, 0]}}}}]}},
      {"$project": {"session_date": 1, "start_ts": 1, "batch_id": 1,
          "claimed": {"$first": "$claim.claimed_count"},
          "observed": {"$round": [{"$first": "$obs.observed"}, 0]},
          "ci_low": {"$first": "$obs.ci_low"}, "ci_high": {"$first": "$obs.ci_high"},
          "frames": {"$first": "$obs.frames"}, "any_uncertain": {"$first": "$obs.any_uncertain"}}},
      {"$addFields": {"delta": {"$cond": [{"$and": ["$claimed", "$observed"]}, {"$subtract": ["$claimed", "$observed"]}, None]}}},
      {"$sort": {"session_date": 1}}]))
# Centre list with open flags / camera status: run TWO grouped aggregations
# (flags by centre_id where status in OPEN; cameras by centre_id) and merge in Python.
# Do NOT run a query per centre (N+1).

# FILE: app/routers/files.py   (evidence + documents; replaces Supabase Storage)
import gridfs
from bson import Binary
from datetime import datetime, timezone
from fastapi import APIRouter, UploadFile, HTTPException, Response
from app.db.mongo import db, oid, verify_file_sig

router = APIRouter()
fs = gridfs.GridFS(db, collection="documents")
ALLOWED = {"application/pdf", "image/jpeg", "image/png"}

def save_evidence(centre_id, jpeg: bytes, eid: str, session_id=None):
    """eid deterministic (ledger hash prefix + index) so a re-sync cannot duplicate."""
    db.evidence_blobs.replace_one({"_id": eid}, {"_id": eid, "centre_id": centre_id, "session_id": session_id,
        "data": Binary(jpeg), "created_at": datetime.now(timezone.utc)}, upsert=True)
    return eid

@router.get("/files/{kind}/{fid}")                       # public, but HMAC-signed and 60 s
def get_file(kind: str, fid: str, exp: int = 0, sig: str = ""):
    if kind not in ("evidence", "document") or not verify_file_sig(kind, fid, exp, sig):
        raise HTTPException(403, detail={"detail": "Link expired", "code": "LINK_EXPIRED"})
    if kind == "evidence":
        d = db.evidence_blobs.find_one({"_id": fid})
        if not d: raise HTTPException(404, detail={"detail": "Not found", "code": "NOT_FOUND"})
        return Response(bytes(d["data"]), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
    g = fs.get(oid(fid))
    return Response(g.read(), media_type=g.content_type or "application/octet-stream",
                    headers={"Cache-Control": "private, no-store"})

@router.post("/public/applications/{code}/documents")     # needs tracking code + phone, max 5 files, 5 MB each
async def upload_doc(code: str, phone: str, file: UploadFile):
    a = db.applications.find_one({"tracking_code": code, "contact_phone": phone})
    if not a or len(a.get("documents") or []) >= 5:
        raise HTTPException(404, detail={"detail": "Not found or limit reached", "code": "NOT_FOUND"})
    if file.content_type not in ALLOWED:
        raise HTTPException(422, detail={"detail": "PDF, JPG or PNG only", "code": "BAD_FILE"})
    data = await file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(422, detail={"detail": "Max 5 MB", "code": "FILE_TOO_LARGE"})
    fid = fs.put(data, filename=file.filename, content_type=file.content_type)
    db.applications.update_one({"_id": a["_id"]}, {"$push": {"documents": {"name": file.filename,
        "file_id": str(fid), "size": len(data), "content_type": file.content_type}}})
    return {"file_id": str(fid)}
# API rule: whenever returning flags/documents, convert evidence_ids / file_id into
# sign_file_url("evidence"|"document", id) strings. Never return raw ids as URLs.

# FILE: app/services/sync.py   (Supabase calls replaced; ledger/signing code unchanged)
import json, os
from bson import ObjectId
from datetime import datetime, timezone
from app.core.config import settings
from app.db.mongo import db
from app.services.ledger import conn, PUBLIC_KEY_HEX
from app.services.dbfuncs import recompute_risk
from app.routers.files import save_evidence

STATE = {"offline": settings.force_offline}
OID_FIELDS = ("centre_id", "session_id", "camera_id", "observation_id")

def register_device():       # call from main.py startup
    db.edge_devices.update_one({"_id": settings.device_id},
        {"$set": {"public_key_hex": PUBLIC_KEY_HEX}, "$setOnInsert": {"created_at": datetime.now(timezone.utc),
         "chain_ok": True}}, upsert=True)

def _fix(row: dict) -> dict:
    row = dict(row)
    for k in OID_FIELDS:
        if row.get(k): row[k] = ObjectId(row[k])
    for k in ("hour_bucket", "created_at"):
        if isinstance(row.get(k), str): row[k] = datetime.fromisoformat(row[k])
    return row

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
            ev_ids = []
            for i, local in enumerate(p.pop("evidence_local", [])):
                with open(local, "rb") as f:
                    ev_ids.append(save_evidence(ObjectId(p["centre_id"]), f.read(), f"{h[:16]}_{i}", p["row"].get("session_id")))
                os.remove(local)
            db.ledger_entries.update_one({"hash": h}, {"$setOnInsert": {"device_id": settings.device_id,
                "seq": seq, "kind": kind, "payload": p, "prev_hash": prev, "sig": sig,
                "ts": datetime.fromisoformat(ts)}}, upsert=True)
            if kind == "observation":
                db.observations.update_one({"ledger_hash": h}, {"$set": _fix(p["row"])}, upsert=True)
            elif kind == "flag":
                row = _fix(p["row"])
                r = db.flags.update_one({"ledger_hash": h}, {"$setOnInsert": {**row, "evidence_ids": ev_ids,
                    "status": "open", "ledger_hash": h, "created_at": datetime.now(timezone.utc),
                    "events": [{"action": "created", "ts": datetime.now(timezone.utc)}]}}, upsert=True)
                if r.upserted_id: recompute_risk(row["centre_id"])
            c.execute("UPDATE ledger SET synced=1 WHERE seq=?", (seq,)); c.commit(); done += 1
        except Exception:
            break          # Atlas unreachable or error: retry next tick, idempotent so nothing duplicates
    db.edge_devices.update_one({"_id": settings.device_id}, {"$set": {"last_sync": datetime.now(timezone.utc)}})
    return {"synced": done, "pending": unsynced_count()}


# FILE: snippets (face.py, flag actions, main.py)
# face.py: store {"_id": staff_id, "embedding_enc": Binary(encrypt(e)), "model": "buffalo_s",
#          "created_at": now}; read with bytes(doc["embedding_enc"]) then decrypt().
#          Deactivating staff: db.staff_faces.delete_one({"_id": sid});
#          db.staff.update_one({"_id": sid}, {"$set": {"active": False, "face_enrolled": False}}).
# Flag action (replaces flag_events table):
#   db.flags.update_one({"_id": oid(fid)}, {"$set": {"status": new_status, "updated_at": now},
#       "$push": {"events": {"actor": oid(user["id"]), "actor_role": user["role"],
#                            "action": action, "comment": comment, "ts": now}}})
#   then recompute_risk(flag["centre_id"]).
# main.py startup: sync.register_device(); scheduler.start();
#   scheduler jobs: sync_tick every 20 s, expire_repairs daily, generate_sessions nightly.
#   include routers: auth, files (+ all earlier routers, now using db instead of sb).
# audit.py: db.audit_log.insert_one({"actor": user["id"], "actor_role": user["role"],
#   "action": action, "entity": entity, "entity_id": str(entity_id), "meta": meta or {},
#   "ts": datetime.now(timezone.utc)})


#------------------------------------

==================================================================
 FRONTEND PATCH: dev_skill_gov AND dev_skill_center
==================================================================
 REMOVE : @supabase/supabase-js, lib/supabase.ts, NEXT_PUBLIC_SUPABASE_* env.
 ADD    : lib/auth.ts, qrcode.react (gov app only, for the MFA QR).
 ENV    : NEXT_PUBLIC_API_URL only.

 Rule changes in the page-plan blocks:
  - "supabase-js is used ONLY for auth" -> "There is NO supabase. Auth is the
    backend: POST /auth/login, /auth/mfa/login, /auth/refresh, /auth/logout."
  - Gov login_id = email. Centre login_id = typed Centre/Trainer ID, sent as is.
    DELETE the "+ @centres.devskill.in" conversion rule.
  - /change-password sends {current_password, new_password}. The response
    contains fresh tokens: store them and continue (no re-login).
  - Gov /settings MFA: POST /me/mfa/setup -> render otpauth_uri as QR
    (qrcode.react) -> user enters the 6-digit code -> POST /me/mfa/enable.
  - Login page MFA step: if the response has mfa_required, show the code
    field and call POST /auth/mfa/login {mfa_token, code}.
  - New error codes in the UX map: ACCOUNT_LOCKED -> "Too many attempts.
    Try again in a few minutes."; INVALID_CREDENTIALS -> "Incorrect ID or
    password"; LINK_EXPIRED (images) -> refetch the entity to get a new URL;
    TOKEN_EXPIRED -> handled silently by refresh in api.ts.
  - Evidence/document URLs are RELATIVE (/files/...). Never put them in
    <img src> directly. Use the useSignedImage hook below.

# FILE: lib/auth.ts
const A = "ds_access", R = "ds_refresh";
export const tokens = {
  get access() { return typeof window === "undefined" ? null : localStorage.getItem(A); },
  get refresh() { return typeof window === "undefined" ? null : localStorage.getItem(R); },
  set(a: string, r: string) { localStorage.setItem(A, a); localStorage.setItem(R, r); },
  clear() { localStorage.removeItem(A); localStorage.removeItem(R); },
};
const BASE = process.env.NEXT_PUBLIC_API_URL!;
const H = { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" };

export async function login(loginId: string, password: string) {
  const r = await fetch(`${BASE}/auth/login`, { method: "POST", headers: H,
    body: JSON.stringify({ login_id: loginId.trim(), password }) });
  const j = await r.json();
  if (!r.ok) throw j.detail ?? j;
  if (j.access_token) tokens.set(j.access_token, j.refresh_token);
  return j;                       // may be { mfa_required, mfa_token }
}
export async function mfaLogin(mfa_token: string, code: string) {
  const r = await fetch(`${BASE}/auth/mfa/login`, { method: "POST", headers: H, body: JSON.stringify({ mfa_token, code }) });
  const j = await r.json(); if (!r.ok) throw j.detail ?? j;
  tokens.set(j.access_token, j.refresh_token); return j;
}
let inflight: Promise<boolean> | null = null;       // single-flight refresh
export function refreshTokens(): Promise<boolean> {
  if (!inflight) inflight = (async () => {
    const rt = tokens.refresh; if (!rt) return false;
    const r = await fetch(`${BASE}/auth/refresh`, { method: "POST", headers: H, body: JSON.stringify({ refresh_token: rt }) });
    if (!r.ok) { tokens.clear(); return false; }
    const j = await r.json(); tokens.set(j.access_token, j.refresh_token); return true;
  })().finally(() => { inflight = null; });
  return inflight;
}
export async function logout() {
  const rt = tokens.refresh;
  if (rt) fetch(`${BASE}/auth/logout`, { method: "POST", headers: H, body: JSON.stringify({ refresh_token: rt }) }).catch(() => {});
  tokens.clear();
}

// FILE: lib/api.ts  (replaces the Supabase-token version)
import { tokens, refreshTokens } from "./auth";
export async function api<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}${path}`, { ...init, headers: {
    "Content-Type": "application/json", "ngrok-skip-browser-warning": "true",
    ...(tokens.access ? { Authorization: `Bearer ${tokens.access}` } : {}), ...init.headers } });
  if (res.status === 401 && retry && (await refreshTokens())) return api<T>(path, init, false);
  if (res.status === 401) { tokens.clear(); window.location.href = "/login"; throw new Error("Signed out"); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = body.detail ?? {};
    if (d.code === "PASSWORD_CHANGE_REQUIRED") window.location.href = "/change-password";
    throw { status: res.status, code: d.code, message: d.detail ?? "Something went wrong" };
  }
  return body as T;
}

// FILE: hooks/useSignedImage.ts   (needed because of the ngrok free-tier interstitial)
import { useEffect, useState } from "react";
export function useSignedImage(relUrl?: string | null) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!relUrl) return; let url: string | null = null; let dead = false;
    fetch(`${process.env.NEXT_PUBLIC_API_URL}${relUrl}`, { headers: { "ngrok-skip-browser-warning": "true" } })
      .then(r => r.ok ? r.blob() : Promise.reject()).then(b => { if (!dead) { url = URL.createObjectURL(b); setSrc(url); } })
      .catch(() => setSrc(null));
    return () => { dead = true; if (url) URL.revokeObjectURL(url); };
  }, [relUrl]);
  return src;       // render skeleton while null; use in <img>, lightbox and thumbnails
}
# AuthGate: if no tokens.access -> /login; else GET /me (api() refreshes if needed);
# role check as before; logout button calls logout() then router.push("/login").

 EDITS TO OLDER BLOCKS (find and replace)
 - Block 1 section 3 and 8: "Supabase Auth/Postgres/Storage/RLS" -> "FastAPI JWT auth,
   MongoDB Atlas, evidence in MongoDB, Argon2, TOTP MFA, locked-down Atlas user".
 - Block 1 section 12: "Login itself needs internet (Supabase Auth)" -> "Login and
   claims need Atlas (internet). Capture, pipeline, ledger work offline."
 - Block 2: delete the DATABASE SCHEMA section and the Supabase client/deps/audit/
   credentials/applications/trainees/sync/face code. Use M1-M4 instead. Rule 2 becomes
   "MongoDB URI and JWT secret live only in backend .env." Folder structure: add
   app/core/security.py, app/db/mongo.py, app/routers/auth.py, app/routers/files.py,
   app/services/dbfuncs.py, scripts/init_db.py (remove supabase_client.py).
 - Block 4/5 rule 2/3, login pages, /change-password: see Frontend Patch above.
 - Block 6A package list: remove @supabase/supabase-js; add qrcode.react (gov).
 - Credential modal and ROI editor, GSAP motion, tokens, layout: NO change.

  - Fire 10 parallel POST /my/trainees at (max-1) enrolled: exactly 1 succeeds, 9 get 409.
 - Double-click Approve: second call returns 409 BAD_STATE, only one centre/login exists.
 - 5 wrong passwords lock the account (429), correct password works after the lock window.
 - Change password: old access and refresh tokens stop working, new tokens work.
 - Refresh token used twice: second use fails (rotation).
 - Expired /files signature returns 403; signed evidence shows in the UI via blob.
 - Kill Atlas connectivity (or FORCE_OFFLINE): demo runs queue in SQLite, then sync once, no duplicates.
 - Trainer token cannot read another centre (403); anon request to any data route gets 401.
 - Mongo shell check: collections list matches M1; TTL indexes exist on evidence_blobs, refresh_tokens.

