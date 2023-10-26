"""A Rich based plugin for pytest."""
from __future__ import annotations

import os
import pickle
import sys
from typing import TYPE_CHECKING

import pytest
from _pytest.runner import (
    CallInfo, pytest_runtest_makereport as make_run_report)
from rich.traceback import Traceback

from .terminal import RichTerminalReporter

if TYPE_CHECKING:
    from _pytest.terminal import TerminalReporter


def pytest_addoption(parser):
    """Add command line option for this plug-in."""
    group = parser.getgroup(
        'rich', 'pytest-richer', after='terminal reporting')
    group.addoption(
        '--rich',
        action='store_true',
        help='Enable rich terminal reporting using pytest-richer.',
    )
    group.addoption(
        '--rich-std-symbols',
        action='store_true',
        help='Use standard symbols for test results progress display.',
    )
    group.addoption(
        '--rich-store-exec-times',
        action='store', metavar='PATH',
        help='Store test execution times in PATH.',
    )


def add_rich_info_to_report(
        report: pytest.Collector | pytest.TestReport,
        call: CallInfo | None = None) -> None:
    """Add a rich_info attribute to a report."""
    report.rich_info = None
    call = call or getattr(report, 'call', None)
    if call and call.excinfo:
        e = call.excinfo
        tb = Traceback.extract(
            exc_type=e.type, exc_value=e.value, traceback=e.tb,
            show_locals=True)

        # We can pickle a Trace, but apparently the xdist serialiser cannot
        # serialise it. So store in pickled form.
        s = pickle.dumps(tb)
        report.rich_info = s


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(
        collector: pytest.Collector) -> None:                    # noqa: ARG001
    """Create a pytest.CollectReport with added 'rich_info' attribute.

    This replicates the standard pytest code to create the report and simply
    adds the extra attribute. Adding an attribute in this way is OK here
    because the test object is expected to carry arbitrary additional
    attributes.
    """
    # Create the report exactly like the pytest code does.
    result = yield
    add_rich_info_to_report(result.get_result())


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_makereport(
        item: pytest.Item, call: CallInfo[None]) -> pytest.TestReport:
    """Create a pytest.TestReport with added 'rich_info' attribute.

    This replicates the standard pytest code to create the report and simply
    adds the extra attribute. Adding an attribute in this way is OK here
    because the test object is expected to carry arbitrary additional
    attributes.
    """
    # Create the report exactly like the pytest code does.
    report = make_run_report(item, call)
    add_rich_info_to_report(report, call)
    return report


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    """Over-ride the --tb option value, as far as pytest code is concerned.

    Note the user's chosen tracback style then override it to be 'short'.
    This ensures that pytest code saves the most useful traceback information.
    This plug-in then takes over the job of interpreting the '--tb' choice
    provided on the command line.

    Doing this here makes sure the value is over-ridden before the xdist
    plugin makes any copies.
    """
    config.option.std_tbstyle = config.option.tbstyle
    if config.option.rich:
        config.option.tbstyle = 'short'


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    """Perform one-time configuration for this plug-in.

    This installs a RichTerminalReporter instance if the user has used the
    `--rich`` command line option.
    """
    # If pytest-xdist is active then our reporter must only be installed
    # for the main process; indicated by PYTEST_XDIST_WORKER not being set.
    if os.environ.get('PYTEST_XDIST_WORKER') is None:            # noqa: SIM102
        if sys.stdout.isatty() and config.getoption('rich'):
            manager = config.pluginmanager
            standard_reporter: TerminalReporter = manager.getplugin(
                'terminalreporter')
            reporter = RichTerminalReporter(config, standard_reporter)
            config.pluginmanager.register(reporter, 'richer-terminal-reporter')


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Disable the standard report just before the test session runs."""
    if os.environ.get('PYTEST_XDIST_WORKER') is None:
        config = session.config
        if sys.stdout.isatty() and config.getoption('rich'):
            standard_reporter = config.pluginmanager.getplugin(
                'terminalreporter')
            if standard_reporter:
                # Monkey patch the standard reporter class to make output
                # producing hook-methods no-operations.
                cls = standard_reporter.__class__
                cls.pytest_collection_finish = nop
                cls.pytest_collection = nop
                cls.pytest_collectreport = nop
                cls.pytest_deselected = nop
                cls.pytest_internalerror = nop
                cls.pytest_runtest_logfinish = nop
                cls.pytest_runtest_logreport = nop
                cls.pytest_runtest_logstart = nop
                cls.pytest_runtest_logstart = nop
                cls.pytest_runtest_logstart = nop
                cls.pytest_runtestloop = nop
                cls.pytest_sessionfinish = nop
                cls.pytest_sessionstart = nop
                cls.pytest_terminal_summary = nop

                # Deregister then register the standard reported plugin so that
                # our monkey patched methods get used as the reporter's hook
                # methods. Note that other code assumes the existence of the
                # standard reporter, so simply deregistering it is not
                # sensible.
                config.pluginmanager.unregister(standard_reporter)
                config.pluginmanager.register(
                    standard_reporter, 'terminalreporter')


def nop(*_args, **_kwargs):
    """Do nothing.

    This is no-operation used to monkey-patch standard reporter methods.
    """
