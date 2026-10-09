from datetime import datetime, timezone
from getpass import getpass

from pymongo import ASCENDING as A
from pymongo import DESCENDING as D

from app.core.security import hash_pw, password_ok
from app.db.mongo import db


def S(*enum_pairs, required=(), **extra):
    props = {k: {"enum": list(v)} for k, v in enum_pairs}
    props.update(extra)
    return {"bsonType": "object", "required": list(required), "properties": props}


VALIDATORS = {
    "users": S(
        ("role", ["super_admin", "scheme_officer", "auditor", "centre_admin", "trainer"]),
        required=["login_id", "password_hash", "role", "full_name", "active"],
    ),
    "centres": S(
        (
            "status",
            ["pending_setup", "awaiting_camera_approval", "live", "suspended", "terminated"],
        ),
        required=["code", "name", "max_trainees", "status"],
    ),
    "flags": S(
        (
            "type",
            [
                "A1",
                "A2",
                "A3",
                "A4",
                "A5",
                "E1",
                "E2",
                "E3",
                "S1",
                "C1",
                "C2",
                "C3",
                "D1",
                "U1",
                "P1",
            ],
        ),
        ("severity", ["low", "medium", "high", "critical"]),
        ("state", ["flagged", "uncertain"]),
        (
            "status",
            [
                "open",
                "acknowledged",
                "centre_responded",
                "under_review",
                "resolved",
                "dismissed",
                "escalated",
            ],
        ),
        required=["centre_id", "type", "severity", "state", "status", "reason"],
    ),
    "applications": S(
        (
            "status",
            ["submitted", "under_review", "query_raised", "resubmitted", "approved", "rejected"],
        ),
        required=["tracking_code", "centre_name", "max_trainees", "status"],
    ),
}

PLAIN = [
    "refresh_tokens",
    "schemes",
    "trade_templates",
    "staff",
    "staff_faces",
    "batches",
    "holidays",
    "trainees",
    "sessions",
    "attendance_claims",
    "staff_attendance",
    "cameras",
    "camera_health_events",
    "frame_fingerprints",
    "edge_devices",
    "ledger_entries",
    "observations",
    "evidence_blobs",
    "repair_declarations",
    "change_requests",
    "notices",
    "audit_log",
    "counters",
]


def ensure(name, validator=None):
    opts = (
        {
            "validator": {"$jsonSchema": validator},
            "validationLevel": "strict",
            "validationAction": "error",
        }
        if validator
        else {}
    )
    if name not in db.list_collection_names():
        db.create_collection(name, **opts)
    elif validator:
        db.command("collMod", name, **opts)


for n, v in VALIDATORS.items():
    ensure(n, v)
for n in PLAIN:
    ensure(n)


def ix(c, keys, **kw):
    db[c].create_index(keys, **kw)


ix("users", [("login_id", A)], unique=True)
ix("users", [("centre_id", A)])
ix("refresh_tokens", [("token_hash", A)], unique=True)
ix("refresh_tokens", [("expires_at", A)], expireAfterSeconds=0)
ix("schemes", [("code", A)], unique=True)
ix("trade_templates", [("scheme_id", A), ("trade_name", A)], unique=True)
ix("applications", [("tracking_code", A)], unique=True)
ix("applications", [("status", A), ("submitted_at", D)])
ix("applications", [("contact_phone", A)])
ix("centres", [("code", A)], unique=True)
ix("centres", [("status", A)])
ix("centres", [("district", A)])
ix("staff", [("centre_id", A)])
ix("staff", [("user_id", A)], unique=True, sparse=True)
ix("batches", [("centre_id", A)])
ix("holidays", [("centre_id", A), ("holiday_date", A)], unique=True)
ix("trainees", [("centre_id", A), ("active", A)])
ix("sessions", [("batch_id", A), ("session_date", A)], unique=True)
ix("sessions", [("centre_id", A), ("session_date", D)])
ix("attendance_claims", [("session_id", A)], unique=True)
ix("staff_attendance", [("session_id", A), ("staff_id", A)], unique=True)
ix("cameras", [("centre_id", A)])
ix("camera_health_events", [("camera_id", A), ("ts", D)])
ix("camera_health_events", [("ts", A)], expireAfterSeconds=90 * 86400)
ix("frame_fingerprints", [("camera_id", A), ("dhash", A)])
ix("frame_fingerprints", [("captured_at", A)], expireAfterSeconds=60 * 86400)
ix("ledger_entries", [("hash", A)], unique=True)
ix("ledger_entries", [("device_id", A), ("seq", A)], unique=True)
ix("observations", [("ledger_hash", A)], unique=True, sparse=True)
ix("observations", [("session_id", A)])
ix("observations", [("centre_id", A), ("hour_bucket", D)])
ix("evidence_blobs", [("centre_id", A), ("created_at", D)])
ix("evidence_blobs", [("created_at", A)], expireAfterSeconds=90 * 86400)
ix("flags", [("ledger_hash", A)], unique=True, sparse=True)
ix("flags", [("centre_id", A), ("created_at", D)])
ix("flags", [("status", A), ("severity", A), ("created_at", D)])
ix("flags", [("type", A)])
ix("notices", [("centre_id", A), ("created_at", D)])
ix("change_requests", [("status", A), ("created_at", D)])
ix("repair_declarations", [("status", A)])
ix("audit_log", [("ts", D)])
ix("audit_log", [("actor", A)])


# ---- seed: scheme + two demo templates ----
if not db.schemes.find_one({"code": "DEMO-SDS"}):
    sid = db.schemes.insert_one(
        {
            "name": "Demo Skill Development Scheme",
            "code": "DEMO-SDS",
            "description": "Seed scheme for the SIH demonstration",
            "active": True,
            "created_at": datetime.now(timezone.utc),
        }
    ).inserted_id
    base = {
        "scheme_id": sid,
        "count_tolerance": 1,
        "agree_ratio": 0.6,
        "min_confidence": 0.35,
        "base_frames": 10,
        "step_frames": 5,
        "max_frames": 40,
        "active": True,
    }
    db.trade_templates.insert_many(
        [
            {
                **base,
                "trade_name": "Sewing and Tailoring",
                "presence_rule": "seated",
                "detector_classes": ["person", "chair", "sewing machine"],
                "equipment_rules": [
                    {
                        "class": "sewing machine",
                        "label": "Sewing machine",
                        "min_units_per_trainee": 0.5,
                        "operability": "in_use",
                        "required": True,
                    },
                    {
                        "class": "chair",
                        "label": "Chair",
                        "min_units_per_trainee": 1.0,
                        "operability": "none",
                        "required": True,
                    },
                ],
            },
            {
                **base,
                "trade_name": "Computer Lab (Basic IT)",
                "presence_rule": "seated",
                "detector_classes": ["person", "chair", "laptop", "tv"],
                "equipment_rules": [
                    {
                        "class": "tv",
                        "label": "Monitor",
                        "min_units_per_trainee": 1.0,
                        "operability": "screen_on",
                        "required": True,
                    },
                    {
                        "class": "chair",
                        "label": "Chair",
                        "min_units_per_trainee": 1.0,
                        "operability": "none",
                        "required": True,
                    },
                ],
            },
        ]
    )

# ---- first super admin (interactive) ----
if not db.users.find_one({"role": "super_admin"}):
    email = input("Super admin email: ").strip().lower()
    pw = getpass("Password (min 10, upper, lower, digit, special): ")
    assert password_ok(pw), "Password too weak"
    db.users.insert_one(
        {
            "login_id": email,
            "password_hash": hash_pw(pw),
            "role": "super_admin",
            "centre_id": None,
            "full_name": "System Admin",
            "must_change_password": False,
            "temp_password_expires_at": None,
            "active": True,
            "token_version": 0,
            "failed_attempts": 0,
            "locked_until": None,
            "mfa_enabled": False,
            "created_at": datetime.now(timezone.utc),
        }
    )

print("DB ready.")
