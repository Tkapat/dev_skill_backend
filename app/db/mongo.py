import hashlib
import hmac
import secrets
import time
from datetime import datetime

from bson import Binary, ObjectId
from fastapi import HTTPException
from pymongo import MongoClient, ReturnDocument

from app.core.config import settings

client = MongoClient(
    settings.mongodb_uri, tz_aware=True, serverSelectionTimeoutMS=8000, maxPoolSize=20
)
db = client[settings.mongodb_db]


def oid(v) -> ObjectId:
    try:
        return ObjectId(str(v))
    except Exception:
        raise HTTPException(422, detail={"detail": "Invalid id", "code": "BAD_ID"}) from None


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
    d = db.counters.find_one_and_update(
        {"_id": name}, {"$inc": {"n": 1}}, upsert=True, return_document=ReturnDocument.AFTER
    )
    return d["n"]


def next_centre_code() -> str:
    return f"DSK-WB-{next_seq('centre'):04d}"


_ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gen_tracking_code() -> str:
    return "APP-" + "".join(secrets.choice(_ALPHA) for _ in range(8))


# ---- signed file URLs (evidence + documents) ----
def sign_file_url(kind: str, fid: str, ttl: int = 60) -> str:
    exp = int(time.time()) + ttl
    sig = hmac.new(
        settings.file_url_secret.encode(), f"{kind}:{fid}:{exp}".encode(), hashlib.sha256
    ).hexdigest()
    return f"/files/{kind}/{fid}?exp={exp}&sig={sig}"  # frontend prepends API URL


def verify_file_sig(kind: str, fid: str, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    good = hmac.new(
        settings.file_url_secret.encode(), f"{kind}:{fid}:{exp}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(good, sig)
