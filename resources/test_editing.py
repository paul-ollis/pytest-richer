"""Demo tests."""

# ruff: noqa: ARG001, W011

import pytest

@pytest.fixture
def setup_fail():
    """Cause setup fixture tp fail fails."""
    pytest.fail('Setup failure for demos.')


@pytest.fixture
def teardown_fail():
    """Cause teardown fixture to fail."""
    yield
    pytest.fail('Teardown failure for demos.')


@pytest.mark.parametrize('value', range(5))
def test_1(value, delay):
    """One of the simple passing tests."""
    delay()


def test_2(setup_fail, delay):
    """A test that has an error during setup."""
    delay()


@pytest.mark.parametrize('value', range(3))
def test_3(value, delay):
    """One of the simple passing tests."""
    delay()


def test_4(teardown_fail, delay):
    """A test that has an error during teardown."""
    delay()
