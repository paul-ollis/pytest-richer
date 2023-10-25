"""Pytest configuration module."""
from __future__ import annotations

import os
import random
import subprocess
import time
from functools import partial
from math import pow
from pathlib import Path
from typing import Callable

import pytest

run_delays: dict[str, float] = {}
setup_delays: dict[str, float] = {}
teardown_delays: dict[str, float] = {}

# Seed the RNG so we get consistent behaviour.
random.seed(12345)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(
        session: pytest.Session,                                 # noqa: ARG001
        exitstatus: int | pytest.ExitCode,                       # noqa: ARG001
    ) -> None:
    """Optionally dump the screen at sessin finish."""
    if os.environ.get('PYTEST_RICHER_SNAP', ''):
        do_dump_window('demo2.png')


def pytest_configure(config):
    config.addinivalue_line(
        'markers', 'fred: An arbitrary mark.')


@pytest.hookimpl
def pytest_collection_modifyitems(
        session: pytest.Session,                                 # noqa: ARG001
        config: pytest.Config,                                   # noqa: ARG001
        items: list[pytest.Item],
    ):
    """Optionally re-order the collected tests."""
    # Set up some test delays so that:
    #
    # 1. Screen shots can capture setup and teardown phases.
    # 2. Running with xdist causes a nice scattering over the progress display.
    def rnd():
        """Generate rand float (0.0 to 1.0), weighted toward small numbers."""
        r = random.random()
        return pow(r, 0.5) * 0.1

    for item in items:
        stage = random.randrange(6)
        if stage == 0:
            setup_delays[item.nodeid] = rnd()
        elif stage == 1:
            teardown_delays[item.nodeid] = rnd()
        else:
            run_delays[item.nodeid] = rnd()


@pytest.fixture(autouse=True)
def delay():
    """Provide a fixture for a delay during test execution."""
    cur_test = os.environ.get('PYTEST_CURRENT_TEST', '')
    nodeid, *_ = cur_test.rpartition(' ' )
    if nodeid:
        delay_s = setup_delays.get(nodeid, 0.0)
        time.sleep(delay_s)
    yield partial(time.sleep, run_delays.get(nodeid, 0.0))
    if nodeid:
        delay_s = teardown_delays.get(nodeid, 0.0)
        time.sleep(delay_s)


@pytest.fixture(autouse=True)
def dump_window() -> Callable[[str], None]:
    """Fixture to allow a window dump (PNG) file to be generated."""
    return do_dump_window


def do_dump_window(path_name: str) -> None:
    """Dump the terminal window to a PNG file."""
    run = partial(subprocess.run, capture_output=True)
    res = run(['/usr/bin/xdotool', 'getwindowfocus'], text=True)
    res = run(['/usr/bin/xwd', '-id', res.stdout.strip()])
    res = run(['/usr/bin/xwdtopnm'], input=res.stdout)
    res = run(['/usr/bin/pnmtopng'], input=res.stdout)
    Path(path_name).write_bytes(res.stdout)
