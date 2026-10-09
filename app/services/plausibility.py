import math


def plausibility(max_trainees: int, equipment: list, rules: list) -> list[str]:
    qty = {e["class"]: e["qty"] for e in equipment}
    warns = []
    for r in rules:
        if not r.get("required"):
            continue
        need = math.ceil(max_trainees * r["min_units_per_trainee"])
        have = qty.get(r["class"], 0)
        if have < need:
            warns.append(f"{r['label']}: {have} listed, template expects at least {need} for {max_trainees} trainees")
    return warns