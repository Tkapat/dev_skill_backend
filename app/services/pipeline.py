import time

from app.services.consolidate import consolidate
from app.services.vision.cleanup import cleanup
from app.services.vision.privacy import blurred_thumbnail
from app.services.vision.quality import quality_gate


def analyse_hour(grab_frame, detector, tpl, cam, staff_subtract, ref_gray=None, sleep=0):
    counts, eq_counts, tamper, thumbs = [], {}, [], []
    taken, attempts, target = 0, 0, tpl["base_frames"]
    prev_gray, last_t = None, 0
    while True:
        while taken < target and attempts < tpl["max_frames"] * 3:
            attempts += 1
            frame = grab_frame()
            if frame is None:
                tamper.append("no_frame")
                continue
            q = quality_gate(frame, prev_gray, time.time() - last_t if last_t else 0, ref_gray)
            prev_gray, last_t = q["gray"], time.time()
            if not q["ok"]:
                tamper.extend(q["reasons"])
                continue
            dets = detector.detect(frame)
            c = cleanup(
                dets,
                frame.shape,
                cam["roi_polygon"],
                cam["zones"],
                tpl["presence_rule"],
                staff_subtract,
            )
            counts.append(c["people_count"])
            for e in c["equipment"]:
                eq_counts.setdefault(e[5], []).append(0)
            per_cls = {}
            for e in c["equipment"]:
                per_cls[e[5]] = per_cls.get(e[5], 0) + 1
            for k, _v in eq_counts.items():
                eq_counts[k][-1] = per_cls.get(k, 0)
            if len(thumbs) < 3:
                thumbs.append(
                    blurred_thumbnail(
                        frame,
                        c["all_people"] and [p[:4] for p in c["all_people"]],
                        c["counted_boxes"],
                    )
                )
            taken += 1
            if sleep:
                time.sleep(sleep)
        res = consolidate(counts, tpl["count_tolerance"], float(tpl["agree_ratio"]))
        if res["consistent"] or taken >= tpl["max_frames"] or attempts >= tpl["max_frames"] * 3:
            break
        target = min(taken + tpl["step_frames"], tpl["max_frames"])
    state = "ok" if res["consistent"] else "uncertain"
    equipment = {
        k: consolidate(v, tpl["count_tolerance"], float(tpl["agree_ratio"]), min_frames=3)
        for k, v in eq_counts.items()
    }
    return {
        "observed": res["observed"],
        "ci_low": res["ci_low"],
        "ci_high": res["ci_high"],
        "state": state,
        "frames_used": taken,
        "per_frame": counts,
        "equipment": equipment,
        "tamper": sorted(set(tamper)),
        "thumbs": thumbs,
    }
