"""Demo tests."""

# ruff: noqa: ARG001

import pytest


@pytest.mark.parametrize('value', range(8))
def test_1(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.xfail(reason="Marked as Xfail with decorator.")
def test_xfail_1(delay):
    """Expected fail test using a decorator."""
    delay()
    pytest.xfail("Marked as Xfail with decorator.")


@pytest.mark.parametrize('value', range(6))
def test_3(value, delay):
    """One of the simple passing tests."""
    delay()


def test_skip_2(delay):
    """Expected fail test using inline call."""
    delay()
    pytest.xfail('Marked as Xfail with inline call to pytest.xfail().')
