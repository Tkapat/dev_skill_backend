import cv2
import numpy as np


def operability(frame, box, rule, persons=()):
    x1, y1, x2, y2 = map(int, box[:4])
    roi = frame[max(0, y1):y2, max(0, x1):x2]
    if roi.size == 0:
        return "unknown"
    if rule == "screen_on":
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        return "present_operable" if (g.mean() > 70 or g.std() > 35) else "present_non_operable"
    if rule == "in_use":
        used = any(min(box[2], p[2]) > max(box[0], p[0]) and min(box[3], p[3]) > max(box[1], p[1]) for p in persons)
        return "present_operable" if used else "present_idle"
    return "present_operable"