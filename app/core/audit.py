from datetime import datetime, timezone
from app.db.mongo import db


def audit(user, action, entity, entity_id, meta=None):
    db.audit_log.insert_one({
        "actor": user["id"],
        "actor_role": user["role"],
        "action": action,
        "entity": entity,
        "entity_id": str(entity_id),
        "meta": meta or {},
        "ts": datetime.now(timezone.utc)
    })