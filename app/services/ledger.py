import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from app.core.config import settings

_lock = threading.Lock()
_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(settings.edge_signing_key_hex))
PUBLIC_KEY_HEX = _key.public_key().public_bytes_raw().hex()
GENESIS = "0" * 64


def conn():
    c = sqlite3.connect(settings.local_db_path, check_same_thread=False)
    c.execute("""CREATE TABLE IF NOT EXISTS ledger(
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT,
        payload TEXT,
        prev_hash TEXT,
        hash TEXT UNIQUE,
        sig TEXT,
        ts TEXT,
        synced INTEGER DEFAULT 0
    )""")
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
        cur = c.execute(
            "INSERT INTO ledger(kind,payload,prev_hash,hash,sig,ts) VALUES(?,?,?,?,?,?)",
            (kind, json.dumps(payload, default=str), prev, h, sig, ts)
        )
        c.commit()
        return {"seq": cur.lastrowid, "hash": h}


def verify_chain(rows: list[dict], public_key_hex: str):
    """rows ordered by seq: dict(kind,payload(dict),prev_hash,hash,sig,ts). Returns (ok, bad_index)."""
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
    prev = GENESIS
    for i, r in enumerate(rows):
        h = hashlib.sha256(canonical({"kind": r["kind"], "payload": r["payload"], "prev": prev, "ts": r["ts"]})).hexdigest()
        try:
            pub.verify(bytes.fromhex(r["sig"]), h.encode())
        except Exception:
            return False, i
        if h != r["hash"] or r["prev_hash"] != prev:
            return False, i
        prev = h
    return True, None