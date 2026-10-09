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
                ops.append(UpdateOne(
                    {"batch_id": b["_id"], "session_date": iso},
                    {"$setOnInsert": {
                        "centre_id": centre_id,
                        "start_ts": st.astimezone(timezone.utc),
                        "end_ts": en.astimezone(timezone.utc),
                        "status": "scheduled",
                        "planned_captures": 0,
                        "done_captures": 0
                    }},
                    upsert=True
                ))
        d += timedelta(days=1)
    return db.sessions.bulk_write(ops).upserted_count if ops else 0


def recompute_risk(centre_id) -> float:
    now = datetime.now(timezone.utc)
    score = 0.0
    for f in db.flags.find({
        "centre_id": centre_id,
        "state": "flagged",
        "status": {"$in": OPEN},
        "created_at": {"$gte": now - timedelta(days=30)}
    }, {"severity": 1, "created_at": 1}):
        score += WEIGHT[f["severity"]] * (1.5 if f["created_at"] >= now - timedelta(days=7) else 1)
    score = round(min(100, score), 2)
    db.centres.update_one({"_id": centre_id}, {"$set": {"risk_score": score}})
    return score


def expire_repairs() -> int:
    return db.repair_declarations.update_many(
        {"status": "approved", "approved_until": {"$lt": date.today().isoformat()}},
        {"$set": {"status": "expired"}}
    ).modified_count


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
        {"$sort": {"session_date": 1}}
    ]))