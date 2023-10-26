"""A Rich based plugin for pytest."""
from __future__ import annotations

import argparse
import os
import pickle
from typing import TYPE_CHECKING

import pytest
from _pytest.runner import (
    CallInfo, pytest_runtest_makereport as make_run_report)
from rich.traceback import Traceback

import pytest_richer

pytest_richer.Logger.context = 'plugin'
dlog = pytest_richer.Logger(
    name='plugin', path_str='plugin', start=False, threaded=True)

print("START", file=dlog)
from pytest_richer.pipe import RichPipeReporter

if TYPE_CHECKING:
    from _pytest.terminal import TerminalReporter

# We have this global flag so that all the hook functions in here can be
# unobtrusive when this plugin is not enabled.
subprocess_mode = False


def in_main_pytest_process() -> bool:
    """Test if this is the main pytest thread, not an xdist worker."""
    return os.environ.get('PYTEST_XDIST_WORKER') is None


def plugin_is_enabled(config: pytest.Config) -> bool:
    """Test if this plugin is enabled."""
    return config.getoption('subprocess_mode', default=False)


def pytest_addoption(parser):
    """Add command line option for this plug-in."""
    group = parser.getgroup(
        'rich', 'pytest-richer', after='terminal reporting')
    group.addoption(
        '--subprocess-mode', action='store_true', help=argparse.SUPPRESS,
    )


def add_rich_traceback_to_report(
        report: pytest.CollectReport | pytest.TestReport,
        call: CallInfo | None = None) -> None:
    """Add a pickled_rich_traceback attribute to a report."""
    report.pickled_rich_traceback = None
    call = call or getattr(report, 'call', None)
    if call and call.excinfo:
        e = call.excinfo
        tb = Traceback.extract(
            exc_type=e.type, exc_value=e.value, traceback=e.tb,
            show_locals=True, locals_hide_dunder=False,
            locals_max_string=256, locals_max_length=100)

        # We can pickle a Trace, but apparently the xdist serialiser cannot
        # serialise it. So store in pickled form.
        s = pickle.dumps(tb)
        report.pickled_rich_traceback = s


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(
        collector: pytest.Collector,
    ) -> pytest.CollectReport | None:                            # noqa: ARG001
    """Create a pytest.CollectReport with 'pickled_rich_traceback' attribute.

    This replicates the standard pytest code to create the report and simply
    adds the extra attribute. Adding an attribute in this way is OK here
    because the CollectReport object is expected to carry arbitrary additional
    attributes.
    """
    # Create the report exactly like the pytest code does. Then add extra
    # information.
    result = yield
    if subprocess_mode:
        add_rich_traceback_to_report(result.get_result())
        return result
    else:
        return None


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_makereport(
        item: pytest.Item, call: CallInfo[None]) -> pytest.TestReport | None:
    """Create a pytest.TestReport with 'pickled_rich_traceback' attribute.

    This replicates the standard pytest code to create the report and simply
    adds the extra attribute. Adding an attribute in this way is OK here
    because the test object is expected to carry arbitrary additional
    attributes.
    """
    if subprocess_mode:
        # Create the report exactly like the pytest code does. Then add extra
        # information.
        report = make_run_report(item, call)
        add_rich_traceback_to_report(report, call)
        return report
    else:
        return None


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: pytest.Config):
    """Over-ride the --tb option value, as far as pytest code is concerned.

    Override the tracback style to be 'short'.
    This ensures that pytest code saves the most useful traceback information.
    Doing this here makes sure the value is over-ridden before the xdist
    plugin makes any copies.

    This also sets the subprocess_mode global for other hook functions to use.
    """
    # pylint: disable=global-statement
    global subprocess_mode                                      # noqa: PLW0603

    if config.getoption('subprocess_mode', default=False):
        subprocess_mode = True                                  # noqa: PLW0603
        config.option.tbstyle = 'short'
    else:
        subprocess_mode = False


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config):
    """Perform one-time configuration for this plug-in.

    This installs a RichTerminalReporter instance if the user has used the
    `--rich`` command line option.
    """
    if plugin_is_enabled(config):
        if not config.option.help:
            if in_main_pytest_process():
                dlog.start()
                print('Start log', file=dlog)

        # If pytest-xdist is active then our reporter must only be installed
        # for the main process.
        if in_main_pytest_process():
            manager = config.pluginmanager
            standard_reporter: TerminalReporter = manager.getplugin(
                'terminalreporter')
            reporter = RichPipeReporter(config, standard_reporter)
            config.pluginmanager.register(reporter, 'subprocess-reporter')


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Disable the standard reporter just before the test session runs."""
    if in_main_pytest_process():
        config = session.config
        if plugin_is_enabled(config):
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
