import statistics
from collections import Counter


def consolidate(counts: list[int], tol: int = 1, agree_ratio: float = 0.6, min_frames: int = 5):
    """Return dict(consistent, observed, ci_low, ci_high, n)."""
    n = len(counts)
    if n < min_frames:
        return {"consistent": False, "observed": None, "ci_low": None, "ci_high": None, "n": n}
    freq = Counter(counts).most_common()
    top = freq[0][1]
    tied = [v for v, c in freq if c == top]
    med = statistics.median(counts)
    mode = min(tied, key=lambda v: abs(v - med))
    agree = [c for c in counts if abs(c - mode) <= tol]
    consistent = len(agree) / n >= agree_ratio
    return {
        "consistent": consistent,
        "observed": mode if consistent else round(med),
        "ci_low": min(agree) if consistent else min(counts),
        "ci_high": max(agree) if consistent else max(counts),
        "n": n,
    }
