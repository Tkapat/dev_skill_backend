import cv2
import numpy as np


def quality_gate(frame, prev_gray=None, prev_gap_s=0, ref_gray=None):
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    reasons = []
    if g.mean() < 25:
        reasons.append("dark")
    if g.std() < 12:
        reasons.append("covered")
    if cv2.Laplacian(g, cv2.CV_64F).var() < 40:
        reasons.append("blurry")
    if (
        prev_gray is not None
        and prev_gap_s >= 20
        and np.abs(g.astype(np.int16) - prev_gray.astype(np.int16)).mean() < 0.5
    ):
        reasons.append("frozen")
    if ref_gray is not None and ref_gray.shape == g.shape:
        (dx, dy), _ = cv2.phaseCorrelate(np.float32(ref_gray), np.float32(g))
        if abs(dx) > 0.05 * g.shape[1] or abs(dy) > 0.05 * g.shape[0]:
            reasons.append("moved")
    return {"ok": not reasons, "reasons": reasons, "gray": g}


def dhash(frame, size=8):
    g = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (size + 1, size))
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return "".join("1" if b else "0" for b in bits)
