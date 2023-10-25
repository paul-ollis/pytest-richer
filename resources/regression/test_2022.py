"""Demo tests."""

# ruff: noqa: ARG001

import pytest


@pytest.mark.parametrize('value', range(41))
def test_1(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(42))
def test_2(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(44))
def test_3(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(42))
def test_4(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(40))
def test_5(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(4))
def test_6(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(4))
def test_7(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(40))
def test_8(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(40))
def test_9(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(40))
def test_10(value, delay):
    """One of the simple passing tests."""
    delay()


@pytest.mark.parametrize('value', range(40))
def test_11(value, delay):
    """One of the simple passing tests."""
    delay()
