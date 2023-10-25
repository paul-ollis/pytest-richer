"""Demo tests."""

# ruff: noqa: ARG001

import pytest


@pytest.mark.parametrize('value', range(11))
def test_1(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(12))
def test_2(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(14))
def test_3(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(12))
def test_4(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(10))
def test_5(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(6))
def test_6(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(8))
def test_7(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(20))
def test_8(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(10))
def test_9(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(20))
def test_10(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(30))
def test_11(value, delay):
    """One of the simple passing tests."""
    delay()
