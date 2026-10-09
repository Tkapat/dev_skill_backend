import cv2
import numpy as np


def _poly(norm, w, h):
    return np.array([[x * w, y * h] for x, y in norm], np.float32).reshape(-1, 1, 2)


def _inside(poly, pt):
    return cv2.pointPolygonTest(poly, pt, False) >= 0


def _ratio(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area_b = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / area_b


def cleanup(dets, shape, roi_norm, zones_norm, presence_rule, staff_to_subtract):
    h, w = shape[:2]
    roi = _poly(roi_norm, w, h) if roi_norm else None
    zones = [_poly(z["polygon"], w, h) for z in (zones_norm or [])]
    people, chairs, objs = [], [], []
    for x1, y1, x2, y2, conf, name in dets:
        pt = ((x1 + x2) / 2, y2) if name == "person" else ((x1 + x2) / 2, (y1 + y2) / 2)
        if roi is not None and not _inside(roi, pt):
            continue
        if name == "person":
            people.append((x1, y1, x2, y2, conf, name))
        elif name == "chair":
            chairs.append((x1, y1, x2, y2, conf, name))
        else:
            objs.append((x1, y1, x2, y2, conf, name))
    counted = []
    for p in people:
        lower = (p[0], p[1] + 0.4 * (p[3] - p[1]), p[2], p[3])
        foot = ((p[0] + p[2]) / 2, p[3])
        if presence_rule == "any":
            ok = True
        elif presence_rule == "seated":
            ok = any(_ratio(lower, c) > 0.25 for c in chairs)
        else:
            ok = any(_inside(z, foot) for z in zones) or any(_ratio(p, o) > 0.2 for o in objs)
        if ok:
            counted.append(p)
    return {
        "people_boxes": [p[:4] for p in people],
        "counted_boxes": [p[:4] for p in counted],
        "people_count": max(0, len(counted) - staff_to_subtract),
        "equipment": [o for o in objs] + ([c for c in chairs]),
        "all_people": people
    }