"""Demo tests."""

# ruff: noqa: ARG001

import pytest


@pytest.mark.parametrize('value', range(10))
def test_1(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.fred()
def test_fred_marked(delay):
    """One of the simple passing tests."""
    delay()
