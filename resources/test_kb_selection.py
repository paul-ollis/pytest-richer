"""Demo tests."""

# ruff: noqa: ARG001

import pytest


@pytest.mark.parametrize('value', range(7))
def test_1(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.xfail(reason="Marked as Xfail with decorator.")
def test_xfail_1(delay):
    """Unexpected passing test."""
    delay()


@pytest.mark.parametrize('value', range(6))
def test_3(value, delay):
    """One of the simple passing tests."""
    delay()
