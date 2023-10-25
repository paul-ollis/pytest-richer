"""Demo tests."""

# ruff: noqa: ARG001

import pytest


@pytest.mark.parametrize('value', range(7))
def test_1(value, dalay):
    """One of the simple passing tests."""
    delay()


def fail_1(delay):
    """One of the simple passing tests."""
    delay()
    assert False


@pytest.mark.parametrize('value', range(2))
def test_1(value, delay):
    """One of the simple passing tests."""
    delay()


def fail_2(delay):
    """One of the simple passing tests."""
    delay()
    assert False


@pytest.mark.parametrize('value', range(70))
def test_1(value, delay):
    """One of the simple passing tests."""
    delay()
