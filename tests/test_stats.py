"""Interval functions against published values (decision doc, testing rule 3)."""

import pytest

from domeware.stats import clopper_pearson, wilson


@pytest.mark.parametrize(("k", "n", "lo", "hi"), [(0, 10, 0.0, 0.3085), (10, 10, 0.6915, 1.0), (5, 10, 0.1871, 0.8129),
                                                  (3, 3, 0.2924, 1.0), (2, 3, 0.0943, 0.9916)])
def test_clopper_pearson_known_values(k, n, lo, hi):
    a, b = clopper_pearson(k, n)
    assert a == pytest.approx(lo, abs=1e-4) and b == pytest.approx(hi, abs=1e-4)


@pytest.mark.parametrize(("k", "n", "lo", "hi"), [(5, 10, 0.2366, 0.7634), (45, 50, 0.7864, 0.9565), (3, 3, 0.4385, 1.0)])
def test_wilson_known_values(k, n, lo, hi):
    a, b = wilson(k, n)
    assert a == pytest.approx(lo, abs=1e-3) and b == pytest.approx(hi, abs=1e-3)


def test_empty_is_the_whole_range():
    assert clopper_pearson(0, 0) == (0.0, 1.0)


def test_cluster_bootstrap_is_wider_than_counting_seconds():
    from domeware.stats import cluster_bootstrap_share

    # four flights of 100 s: two fully tracked, two not at all. Counting 400 seconds as independent gives a tight
    # interval around 50 %; by flight, the honest interval is wide.
    lo, hi = cluster_bootstrap_share([100, 100, 0, 0], [100, 100, 100, 100], seed=1)
    a, b = clopper_pearson(200, 400)
    assert lo < a and hi > b and lo <= 0.25 and hi >= 0.75
    assert cluster_bootstrap_share([5, 5], [10, 10]) is None  # two flights: no interval
