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
