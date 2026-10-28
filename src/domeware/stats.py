"""Confidence intervals for shares (decision D13): Clopper-Pearson everywhere, always shown with n.

Wilson is kept only as a tested function and is not shown, so claims never mix two methods.
"""

from __future__ import annotations

import math

from scipy.stats import beta, norm


def clopper_pearson(k: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Exact two-sided interval for k successes in n trials. (0, 1) when n is 0."""
    if n <= 0:
        return 0.0, 1.0
    a = (1 - conf) / 2
    lo = 0.0 if k <= 0 else float(beta.ppf(a, k, n - k + 1))
    hi = 1.0 if k >= n else float(beta.ppf(1 - a, k + 1, n - k))
    return lo, hi


def wilson(k: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Wilson score interval. Not shown anywhere (D13); kept for cross-checks."""
    if n <= 0:
        return 0.0, 1.0
    z = float(norm.ppf(1 - (1 - conf) / 2))
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, c - h), min(1.0, c + h)


def cluster_bootstrap_share(detected: list[int], totals: list[int], reps: int = 2000, conf: float = 0.95,
                            seed: int = 0) -> tuple[float, float] | None:
    """Interval for a pooled share when the trials come in clusters (seconds inside one drone flight are not
    independent; flights are). Resamples whole clusters. None with fewer than 3 clusters: too few for an interval."""
    import numpy as np

    k, n = np.asarray(detected, float), np.asarray(totals, float)
    if len(n) < 3 or n.sum() == 0:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(n), size=(reps, len(n)))
    shares = k[idx].sum(axis=1) / np.maximum(n[idx].sum(axis=1), 1)
    a = (1 - conf) / 2
    return float(np.quantile(shares, a)), float(np.quantile(shares, 1 - a))
