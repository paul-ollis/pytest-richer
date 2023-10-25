"""Support for compatability with cor pytest and plugins."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def run_phase_suppressed(config: pytest.Config) -> bool:
    """Determine if the run phase has been suppressed.

    This tries to work out, based on things liek command line options, whether
    the normal test execution phase will be performed.
    """
    for name in ('showfixtures', 'show_fixtures_per_test'):
        if config.getoption(name, None):
            return True
    return False
