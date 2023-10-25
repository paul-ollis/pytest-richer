"""Demo tests."""

# ruff: noqa: ARG001, S101

import pytest


def sub_func():
    """Help a test do its thing."""
    a = 3
    assert a == 2                                               # noqa: PLR2004


def another_sub_func():
    """Help a test do its thing."""
    pass
    pass
    pass
    pass
    pass
    pass
    a = 3
    print(a / 0)


@pytest.mark.parametrize('value', range(15))
def test_1(value, delay):
    """One of the simple passing tests."""
    delay()


def xtest_2(delay):
    """A plain failing test."""
    delay()
    pytest.fail('This test has failed!')


def test_3(delay):
    """A plain failing test."""
    delay()
    another_sub_func()
