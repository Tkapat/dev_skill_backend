import json
import os
from bson import ObjectId
from datetime import datetime, timezone
from app.core.config import settings
from app.db.mongo import db
from app.services.ledger import conn, PUBLIC_KEY_HEX
from app.services.dbfuncs import recompute_risk
from app.routers.files import save_evidence

STATE = {"offline": settings.force_offline}
OID_FIELDS = ("centre_id", "session_id", "camera_id", "observation_id")


def register_device():
    db.edge_devices.update_one({"_id": settings.device_id},
        {"$set": {"public_key_hex": PUBLIC_KEY_HEX}, "$setOnInsert": {
            "created_at": datetime.now(timezone.utc), "chain_ok": True}}, upsert=True)


def _fix(row: dict) -> dict:
    row = dict(row)
    for k in OID_FIELDS:
        if row.get(k):
            row[k] = ObjectId(row[k])
    for k in ("hour_bucket", "created_at"):
        if isinstance(row.get(k), str):
            row[k] = datetime.fromisoformat(row[k])
    return row


def unsynced_count():
    return conn().execute("SELECT COUNT(*) FROM ledger WHERE synced=0").fetchone()[0]


def sync_tick(batch=25):
    if STATE["offline"]:
        return {"skipped": "offline"}
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
            db.ledger_entries.update_one({"hash": h}, {"$setOnInsert": {
                "device_id": settings.device_id,
                "seq": seq,
                "kind": kind,
                "payload": p,
                "prev_hash": prev,
                "sig": sig,
                "ts": datetime.fromisoformat(ts)
            }}, upsert=True)
            if kind == "observation":
                db.observations.update_one({"ledger_hash": h}, {"$set": _fix(p["row"])}, upsert=True)
            elif kind == "flag":
                row = _fix(p["row"])
                r = db.flags.update_one({"ledger_hash": h}, {"$setOnInsert": {
                    **row,
                    "evidence_ids": ev_ids,
                    "status": "open",
                    "ledger_hash": h,
                    "created_at": datetime.now(timezone.utc),
                    "events": [{"action": "created", "ts": datetime.now(timezone.utc)}]
                }}, upsert=True)
                if r.upserted_id:
                    recompute_risk(row["centre_id"])
            c.execute("UPDATE ledger SET synced=1 WHERE seq=?", (seq,))
            c.commit()
            done += 1
        except Exception:
            break
    db.edge_devices.update_one({"_id": settings.device_id}, {"$set": {"last_sync": datetime.now(timezone.utc)}})
    return {"synced": done, "pending": unsynced_count()}