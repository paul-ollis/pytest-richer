"""Demo tests."""

# ruff: noqa: ARG001

import pytest


@pytest.mark.parametrize('value', range(3))
def test_1(value):
    """One of the simple passing tests."""


@pytest.mark.skip(reason='Skipping this test with decorator.')
def test_skip_1():
    """Skipped test using a decorator."""


@pytest.mark.parametrize('value', range(6))
def test_3(value):
    """One of the simple passing tests."""


def test_skip_2():
    """Skipped test using a decorator."""
    pytest.skip('Skipping this test with inline call to pytest.skip().')
