import math
SEV = ["low", "medium", "high", "critical"]


def margin(claimed, tol):
    return max(tol, math.ceil(0.10 * claimed))


def severity(gap_ratio, repeats):
    base = 0 if gap_ratio < .15 else 1 if gap_ratio < .30 else 2 if gap_ratio < .50 else 3
    return SEV[min(3, base + (1 if repeats >= 3 else 0))]


def evaluate(ctx: dict) -> list[dict]:
    """ctx: claimed, enrolled, sanctioned_max, tol, obs{observed,ci_low,ci_high,state,equipment},
       equipment_rules[{class,label,sanctioned,declared}], repairs{class:qty}, repeats{type:int},
       trainer_verified(bool), camera_tamper(list)"""
    flags, o, c = [], ctx["obs"], ctx["claimed"]
    if ctx["camera_tamper"] and o["observed"] is None:
        return [{
            "type": "C1",
            "severity": "high",
            "state": "flagged",
            "reason": f"Camera quality problem: {', '.join(ctx['camera_tamper'])}. Counts not evaluated."
        }]
    if not ctx["trainer_verified"]:
        flags.append({
            "type": "S1",
            "severity": "medium",
            "state": "flagged",
            "reason": "No face-verified trainer for this session."
        })
    if o["state"] == "uncertain":
        flags.append({
            "type": "U1",
            "severity": "low",
            "state": "uncertain",
            "reason": f"Frames disagree ({o['ci_low']}..{o['ci_high']}) even after max frames. Needs review."
        })
    elif c is not None:
        m = margin(c, ctx["tol"])
        if c > 0 and o["observed"] == 0:
            flags.append({
                "type": "A4",
                "severity": "critical",
                "state": "flagged",
                "reason": f"Session claimed {c} present but camera saw nobody."
            })
        elif c > o["ci_high"] + m:
            gap = (c - o["observed"]) / max(c, 1)
            flags.append({
                "type": "A1",
                "severity": severity(gap, ctx["repeats"].get("A1", 0)),
                "state": "flagged",
                "reason": f"Claimed {c}, camera observed {o['observed']} (range {o['ci_low']}-{o['ci_high']}, margin {m})."
            })
        elif o["observed"] > c + m:
            flags.append({
                "type": "A5",
                "severity": "low",
                "state": "flagged",
                "reason": f"Camera saw {o['observed']} but only {c} claimed."
            })
        if c > ctx["enrolled"]:
            flags.append({
                "type": "A3",
                "severity": "medium",
                "state": "flagged",
                "reason": f"Claimed {c} but only {ctx['enrolled']} enrolled."
            })
    if o["state"] == "ok" and o["ci_low"] is not None and o["ci_low"] > ctx["sanctioned_max"]:
        flags.append({
            "type": "A2",
            "severity": "high",
            "state": "flagged",
            "reason": f"Observed {o['observed']} exceeds sanctioned maximum {ctx['sanctioned_max']}."
        })
    for r in ctx["equipment_rules"]:
        expected = r["sanctioned"] - ctx["repairs"].get(r["class"], 0)
        e = o["equipment"].get(r["class"])
        if r["declared"] != r["sanctioned"]:
            flags.append({
                "type": "E3",
                "severity": "low",
                "state": "flagged",
                "reason": f"{r['label']}: declared {r['declared']} vs sanctioned {r['sanctioned']}."
            })
        if e and e["consistent"] and e["ci_high"] < expected:
            gap = (expected - e["observed"]) / max(expected, 1)
            flags.append({
                "type": "E1",
                "severity": severity(gap, ctx["repeats"].get("E1", 0)),
                "state": "flagged",
                "reason": f"{r['label']}: sanctioned {expected}, camera saw {e['observed']}."
            })
        elif e and not e["consistent"]:
            flags.append({
                "type": "U1",
                "severity": "low",
                "state": "uncertain",
                "reason": f"{r['label']} count unstable across frames."
            })
    return flags