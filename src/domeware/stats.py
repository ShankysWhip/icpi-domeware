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
