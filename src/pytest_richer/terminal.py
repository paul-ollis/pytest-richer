"""An alternative TerminalReporter providing Rich text based output."""
from __future__ import annotations

import importlib.util
import os
import threading
import time
import weakref
from pathlib import Path
from typing import Callable, Literal, TYPE_CHECKING, cast

import pytest
from _pytest.config import ExitCode
from _pytest.python import Function

from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from . import compat
from .header import generate_header_panel
from .helpers import Helper, NodeResult, ReporterBase
from .progress import (
    DescriptiveProgressBar, GraphBar, PercentageProgressBar,
    SlottedProgressBar, TestProgressMapper)

if TYPE_CHECKING:
    import warnings
    from collections.abc import Iterator, Sequence

    from _pytest.reports import BaseReport, CollectReport, ExceptionRepr
    from _pytest.terminal import TerminalReporter

HAVE_XDIST = importlib.util.find_spec('xdist') is not None
HORIZONTAL_PAD = (0, 1, 0, 1)

summary_exit_codes = {
    ExitCode.OK,
    ExitCode.TESTS_FAILED,
    ExitCode.INTERRUPTED,
    ExitCode.USAGE_ERROR,
    ExitCode.NO_TESTS_COLLECTED,
}

# This is here for ad-hoc debugging. Any strings in this list get printed in
# the final summary box.
messages: list[str] = []


def in_main_thread():
    """Test if this is the main (driver) thread."""
    return threading.main_thread().ident == threading.current_thread().ident


def interpret_report(report: BaseReport) -> tuple[str, str]:
    """Interpret some details of a report.

    This is here to provide compatability with the standard terminal reporter.
    It is largely cut-and-paste from Pytest code.

    :return:
        A tuple of strings; outcome, letter-code.
    """
    letter = 'F'
    if report.passed:
        letter = '.'
    elif report.skipped:
        letter = 's'

    outcome: str = report.outcome
    if report.when in ('collect', 'setup', 'teardown') and outcome == 'failed':
        outcome = 'error'
        letter = 'E'

    return outcome, letter


class TimeStatCollector:
    """A collector of timing statistics."""

    def __init__(self):
        self.stats: dict[str, list[float, float]] = {}

    def start(self, name):
        """Start timing something."""
        self.stats[name] = [time.time(), None]

    def stop(self, name):
        """Stop timing something."""
        if name in self.stats and self.stats[name][1] is None:
            self.stats[name][1] = time.time()

    def __iter__(self) -> Iterator[tuple[str, float]]:
        """Iterate over the timeing stats."""
        for name, (start, stop) in self.stats.items():
            if stop is not None:
                yield name, stop - start


class Collection(Helper):
    """Information about and management of collected tests."""

    # pylint: disable=too-many-instance-attributes
    prog_mapper: TestProgressMapper

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.failed: dict[str, CollectReport] = {}
        self.skipped: dict[str, Collection] = {}
        self.items: dict[str, pytest.Item] = {}
        self.deselected: set[str] = set()
        self.collection_thread: threading.Thread | None = None
        self.processed_reports: set[str] = set()
        self.active_xdist_nodes: int | None = self.numprocesses
        self.started = False

    def report_already_seen(self, report) -> bool:
        """Check if a report has already been seen.

        :return: True if the report has already been seen.
        """
        seen = report.nodeid in self.processed_reports
        self.processed_reports.add(report.nodeid)
        return seen

    def start(self):
        """Prepare for the start of test collection."""
        self.progress.add_bar(
            'collection', bar=DescriptiveProgressBar(label='Collection'))

        if self.numprocesses is not None:
            # Looks like we are running under pytest-xdist. Do an extra
            # parallel collecion so that we have full test details. The
            # pytest-xdist plugin 'optimizes' away collection information so
            # this is the only way to provide real-time collection stats,
            # albeit a somewhat misleading ones.
            #
            # The separate thread allows the pytest-xdist driver to continue
            # managing its child processes.
            def do_collect():
                pts = self.pytest_session
                pts.items = pts.perform_collect()

            self.collection_thread = threading.Thread(
                target=do_collect, name='par-collect')
            self.collection_thread.start()
        self.started = True

    if HAVE_XDIST:
        def xdist_node_finished(self, node, ids: list[str]):
            """Process pytest-xdist node collection complete notification."""
            self.active_xdist_nodes -= 1
            if self.active_xdist_nodes == 0:
                self.cleanup()
                session = self.pytest_session
                if self.failed and not self.continue_on_error:
                    n_failures = len(self.failed)
                    plural = 's' if n_failures > 1 else ''
                    msg = f'{n_failures} error{plural} during collection'
                    raise session.Interrupted(msg)

    def finish(self):
        """Process the completion of test collection.

        When running an extra, parallel connection (because pytest-xdist is on
        control of test collection), this gets invoked in the collection
        thread, so joining of the thread must be performed by other code
        invoking the `cleanup` method.
        """
        if self.progress is not None:
            self.update_progress(final=True)
        self.prog_mapper = TestProgressMapper(self, self.items)

    def cleanup(self) -> None:
        """Clean up after collection.

        Called by various reporting hooks because the `finish` is not in a
        position to join for the collection threead.
        """
        if self.collection_thread:
            self.collection_thread.join()
            self.collection_thread = None

    def handle_report(self, report: pytest.CollectReport) -> None:
        """Handle a report on test collection progress.

        This is normally invoked directly by the PyTest framework, but
        pytest-xdist will also invoke this when collection errors occurr. So we
        need to handle multiple reports with the same ``report.nodeid`` in a
        graceful manner.

        :report:
            The is a pytest.CollectReport object. The important attributes are:

            result
                A list of pytest.Item and pytest.Collector instances. The
                pytest.Item instances represent actual tests.
            outcome
                A string that can be 'passed', 'skipped or 'failed'.
            longrepr
                If the outcome 'skipped' then this is as a tuple of:

                    path_name: str, lineno: int, message: str

                If outcome is 'failed' then this is either an
                ExceptionChainRepr or a simple CollectErrorRepr. The latter
                simply contains a formatted exception in its ``longrepr``
                string attribute.
        """
        item_types = pytest.Item, Function
        if not self.report_already_seen(report):
            if report.outcome == 'failed':
                self.failed[report.nodeid] = report
            elif report.outcome == 'skipped':
                self.skipped[report.nodeid] = report
            else:
                items = [
                    x for x in report.result if isinstance(x, item_types)]
                for item in items:
                    self.items[item.nodeid] = item
                self.update_progress(final=False)

    def deselect(self, items: Sequence[pytest.Item]) -> None:
        """Handle deselection of one or more tests."""
        for item in items:
            self.deselected.add(item.nodeid)
            self.items.pop(item.nodeid)

    def update_progress(self, *, final: bool = False):
        """Update the test collection progress."""
        if self.progress is None:
            return                                          # pragma: defensive

        deselected = len(self.deselected)
        failed = len(self.failed)
        skipped = len(self.skipped)
        selected = len(self.items) - deselected
        leader = 'complete' if final else 'running'
        desc = f'[cyan][bold]{leader}[/]: [/]selected=[green]{selected}[/]'
        if skipped:
            desc += f' skipped=[blue]{skipped}[/]'
        if deselected:
            desc += f' deselected=[yellow]{deselected}[/]'
        if failed:
            desc += f' failed=[red]{failed}[/]'

        self.progress.update('collection', text=desc, refresh=final)

    def report_summary(self):
        """Report a summary of any test collection failures."""
        self.report_failures(
            self.failed.values(), typename='collection',
            title='Collection Errors', style='')

    @property
    def continue_on_error(self):
        """The value of the global continue_on_collection_errors option."""
        return self.pytest_session.config.option.continue_on_collection_errors


class RunPhase(Helper):
    """Information about and management of the test execution phase."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.node_to_result: dict[str, NodeResult] = {}
        self.path_to_items: dict[Path, dict[str, pytest.Item]] = {}
        self.path_width: int = 0
        self.num_tests_executed = 0
        self.running_tests: set[str] = set()
        self.name_width = 10
        self.start_times: dict[str, float] = {}
        self.exec_times: dict[str, float] = {}
        self.svg_created = False

    @property
    def group_by_dir(self):
        """True if tests are group by directory."""
        return self.collection.group_by_dir

    def start(self):
        """Prepare for the test execution phase."""
        if self.collection.failed:
            return

        # Create a mapping from test script path to another dict mapping nodeid
        # to test item.
        for nodeid, item in self.collection.items.items():
            self.node_to_result[nodeid] = NodeResult(weakref.proxy(self))
            path, _ = self.parse_nodeid(nodeid)
            if path not in self.path_to_items:
                self.path_to_items[path] = {}
            self.path_to_items[path][nodeid] = item

        # Add a progress bar for each grouping of tests.
        prog_mapper = self.collection.prog_mapper
        self.name_width = prog_mapper.name_width
        for name, items in prog_mapper.item_map.items():
            label = prog_mapper.label_map.get(name, name)
            bar = SlottedProgressBar(label=label, nitems=len(items))
            self.progress.add_bar(name, bar=bar)

        # Add progress bars for the overall progress and current number of
        # active tes runners, The latter only if pytest-xdist is active.
        bar = PercentageProgressBar(
            label='Overall', nitems=len(self.node_to_result))
        self.progress.add_bar('overall', bar=bar)
        if self.numprocesses is not None and self.numprocesses > 1:
            bar = GraphBar(
                label='Parallel test count', max_count=self.numprocesses)
            self.progress.add_bar('running', bar=bar)

    def log_test_start(
            self, nodeid: str, location: tuple[str, int | None, str],
        ) -> None:
        """Note the starting of a given test."""
        if nodeid in self.deselected:
            return                                          # pragma: defensive
        result: NodeResult
        result, name = self.get_node_result(nodeid)
        if not result:
            return                                          # pragma: defensive

        result.started = True
        self._update_progress(name)
        self.running_tests.add(name)
        self.start_times[nodeid] = time.time()

    def log_report(self, report: pytest.TestReport) -> None:
        """Handle a report for a given running test.

        This is invoked multiple times for a given test The ``when`` attribute
        identifies the test's phase; 'setup', 'call' and 'teardown'. This is
        always invoked for the setup and teardown phases, but is omitted for
        the call phase if the setup phase failed.
        """
        nodeid = report.nodeid
        if nodeid in self.deselected:
            return                                          # pragma: defensive

        result: NodeResult
        result, name = self.get_node_result(nodeid)
        if result:
            setattr(result, report.when, report)
            self._update_progress(name)

    def log_test_end(self, nodeid: str) -> None:
        """Note the completion of one test."""
        if nodeid in self.start_times:
            self.exec_times[nodeid] = time.time() - self.start_times[nodeid]
        self.num_tests_executed += 1
        _result, name = self.get_node_result(nodeid)
        self.running_tests.discard(name)

    def _update_progress(self, nodeid: str = '', *, refresh: bool = False):
        result: NodeResult
        result, name = self.get_node_result(nodeid)
        if not result:
            return                                          # pragma: defensive

        group_name = self.collection.prog_mapper.group(name)
        path_items = self.collection.prog_mapper.items_for_test_group(name)

        results = [self.node_to_result[item.nodeid] for item in path_items]
        tot_finished = len([res for res in results if res.finished])
        idx = results.index(result)
        self.progress.update(
            group_name, count=tot_finished, idx=idx, char_str=result.indicator)

        tot_finished = len([
            res for res in self.node_to_result.values() if res.finished])
        self.progress.update('overall', count=tot_finished)
        self.progress.update(
            'running', count=len(self.running_tests),
            refresh=tot_finished >= len(self.node_to_result))

    def finish(self):
        """Handle completion of the execution phase."""
        if self.progress:
            self.running_tests = {}
            self.progress.update('running', count=0)
            self.progress.stop()
        times_path_name = self.config.getoption('rich_store_exec_times')
        if times_path_name:
            times = [(t, nodeid) for nodeid, t in self.exec_times.items()]
            with Path(times_path_name).open('wt', encoding='utf-8') as f:
                for t, nodeid in sorted(times, reverse=True):
                    f.write(f'{t:.3f} {nodeid}\n')

    def report_summary(self):
        """Report a summary of any run-phase failures."""
        self.report_failures(
            self.failed, 'run-phase', title='Test Failures',
            style=self.style_map.get('error', ''))
        self.report_failures(
            self.setup_errored, 'run-phase', title='Setup Errors',
            style=self.style_map.get('setup_errored', ''))
        self.report_failures(
            self.teardown_errored, 'run-phase', title='Teardown Errors',
            style=self.style_map.get('teardown_errored', ''))

    def get_node_result(self, nodeid: str) -> tuple[NodeResult | None, str]:
        """Get the result for a given node ID.

        When running under pytest-xdist with 'grouped' tests the reported ID
        may have an append '@<group-name>'. This method handles removing the
        group name part as required.
        """
        name = nodeid
        result = self.node_to_result.get(nodeid)
        if not result:
            root, *_ = nodeid.rpartition('@')
            result = self.node_to_result.get(root)
            if result:
                name = root
        return result, name

    @property
    def collection(self) -> bool:
        """The test collection information."""
        return self.reporter.collection

    @property
    def deselected(self) -> set[str]:
        """The nodeid set of deselected tests."""
        return self.reporter.collection.deselected

    @property
    def not_run(self) -> Sequence[NodeResult]:
        """The results for tests that did not run (no teardown)."""
        return [res for res in self.node_to_result.values()
            if not res.teardown]

    @property
    def passed(self) -> Sequence[NodeResult]:
        """The results for the passing tests."""
        return [res for res in self.node_to_result.values()
            if res.passed_report and not res.xpassed_report
                and res.finished]

    @property
    def setup_errored(self) -> Sequence[NodeResult]:
        """The results for the tests failed during setup."""
        return [res for res in self.node_to_result.values()
            if res.setup_error_report]

    @property
    def teardown_errored(self) -> Sequence[NodeResult]:
        """The results for the tests that failed during teardown."""
        return [res for res in self.node_to_result.values()
            if res.teardown_error_report]

    @property
    def failed(self) -> Sequence[NodeResult]:
        """The results for the tests that failed."""
        return [res for res in self.node_to_result.values()
            if res.failed_report and not res.xfailed_report
                and not res.setup_error_report]

    @property
    def skipped(self) -> Sequence[NodeResult]:
        """The results for the tests that were skipped."""
        return [res for res in self.node_to_result.values()
            if res.skipped_report and not res.xfailed_report]

    @property
    def xfailed(self) -> Sequence[NodeResult]:
        """The results for the tests that failed as expected."""
        return [res for res in self.node_to_result.values()
            if res.xfailed_report]

    @property
    def xpassed(self) -> Sequence[NodeResult]:
        """The results for the tests that failed as expected."""
        return [res for res in self.node_to_result.values()
            if res.xpassed_report]


class Session(Helper):
    """Information about and management of a test session."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.interrupt = None
        self.start_time: float = time.time()

    def start(self, session: pytest.Session) -> None:
        """Handle the start of the session.

        This is the first notification; it occurs before test collection.
        """
        if not self.no_header:
            header = generate_header_panel(session)
            self.console.print(header)

    def finish(self, exitstatus: int | pytest.ExitCode) -> None:
        """Handle the completion of a test session."""
        self.collection.cleanup()

        if self.collection.failed:
            self.collection.report_summary()
        if exitstatus == ExitCode.INTERRUPTED:
            self.console.print(f'Run was interrupted: {self.interrupt.value}')

        if not compat.run_phase_suppressed(self.config):
            self.run_phase.report_summary()
            self.print_summary()
            # TODO: Check that we replicate all necessary
            #       pytest_terminal_summary output.
            if exitstatus in summary_exit_codes and not self.no_summary:
                self.config.hook.pytest_terminal_summary(
                    terminalreporter=self.reporter, exitstatus=exitstatus,
                    config=self.config)

    # TODO: Set up color scheme.
    def print_summary(self):
        """Print the general summary of the test run."""
        def add_row(value, text, style):
            a = Padding(str(value), pad=HORIZONTAL_PAD, style=style)
            b = Padding(text, pad=HORIZONTAL_PAD, style='default')
            table.add_row(a, b, style='default')

        table = Table.grid()
        table.add_column(justify='right', no_wrap=True)
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)

        add_row(
            len(self.collection.items) + len(self.collection.deselected),
            'Total tests', 'default')
        self._add_test_summaries(add_row)

        for name, t in self.reporter.time_stats:
            add_row(f'{t:6.2f}s', name, 'default')
        elapsed = f'{time.time() - self.start_time:6.2f}s'
        add_row(elapsed, 'Overall', 'default')
        for m in messages:
            add_row('', m, 'default')                        # pragma: no cover

        panel = Panel(
            table, title='Summary', style='bold blue', expand=False,
            border_style='bold blue')
        self.console.print(panel)

    def _add_test_summaries(self, add_row: Callable[[int, str, str], None]):
        """Add the summaries for test results, passed, failed, *etc*."""
        if self.collection.deselected:
            add_row(len(self.collection.deselected), 'Deselected',
            self.style_map.get('deselected', ''))
        type_names = (
            'passed', 'failed', 'xfailed', 'xpassed', 'not_run', 'skipped',
            'setup_errored', 'teardown_errored')
        name_to_label = {
            'xfailed': 'Expected failures',
            'xpassed': 'Unexpected passes',
            'setup_errored': 'Setup errors',
            'teardown_errored': 'Teardown errors',
        }
        for state in type_names:
            if seq := getattr(self.run_phase, state):
                label = name_to_label.get(
                    state, state.capitalize().replace('_', ' '))
                indicator = self.indicator_map.get(state, '?')
                style = self.style_map.get(state, '')
                if indicator:
                    if style:
                        label = f'{label} ([{style}]{indicator}[/])'
                    else:
                        label = f'{label} ({indicator})'
                add_row(len(seq), label, style)

    def handle_keyboard_interrupt(
            self, excinfo: pytest.ExceptionInfo[BaseException]) -> None:
        """Handle a keyboard or rleated interrupt."""
        self.interrupt = excinfo

    @property
    def no_header(self) -> bool:
        """The no-header command line option value."""
        return self.config.getoption('no_header')

    @property
    def collection(self) -> RunPhase:
        """The Collection handler."""
        return self.reporter.collection

    @property
    def run_phase(self) -> RunPhase:
        """The RunPhase handler."""
        return self.reporter.run_phase


class RichTerminalReporter(ReporterBase):
    """A replacement for the standard pytest terminal reporter.

    This needs to provide quite a large number of hook methods. Most of them
    simply hand off to `Helper` based objects.
    """

    # pylint: disable=too-many-public-methods
    Status = Literal['collected', 'running', 'success', 'fail', 'error']

    def __init__(self, config: pytest.Config, std_reporter: TerminalReporter):
        super().__init__(config)
        self.std_reporter = std_reporter
        self.pytest_session = None
        self.collection = Collection(self)
        self.run_phase = RunPhase(self)
        self.session = Session(self)
        self.monkey_patch_terminal_reporter()
        self.time_stats = TimeStatCollector()
        self.time_stats.start('Init phase')

        # This is required to support some standard pytest features. Currently
        # I know that the --duration option makesuse of this.
        self.stats: dict[str, list] = {}

    def monkey_patch_terminal_reporter(self):
        """Patch parts of the terminal reporter.

        As far as I can tell, it is basically impossible to completely override
        that standard pytest terminal reporter in a completely clean way.

        This is my practical solution.
        """
        self.std_reporter.write = self.write
        self.std_reporter.rewrite = self.rewrite
        self.std_reporter.write_line = self.write_line

    @property
    def _tw(self) -> pytest.TerminalWriter:
        """The terminal write emulation.

        This provides compatability for other parts of pytest and plugins.
        """
        return cast(pytest.TerminalWriter, self.formatter)

    ## Overall session management.
    @pytest.hookimpl(trylast=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        """Perform required actions at the start of the session."""
        self.pytest_session = session
        self.session.start(session)
        if os.environ.get('PYTEST_RICHER_FORCE_ERROR', ''):
            msg = 'Just for testing'
            raise RuntimeError(msg)

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(
            self, session: pytest.Session, exitstatus: int | pytest.ExitCode,
        ) -> None:
        """Perform required actions at the end of the session."""
        self.time_stats.stop('Execution')
        self.run_phase.finish()
        self.session.finish(exitstatus)

    @property
    def numprocesses(self) -> int | None:
        """The number of pytest-xdist processes.

        :return:
            The value ``None`` indicates that pytest-xdist is not being used.
            Otherwise this is an integer >= 1.
        """
        return self.config.getoption('numprocesses', None)

    if HAVE_XDIST:
        @pytest.hookimpl(trylast=True)
        def pytest_xdist_node_collection_finished(
                self, node: str, ids: list[str]) -> None:
            """Handle a pytest-xdist worker completing its collection phase."""
            self.collection.xdist_node_finished(node, ids)

    @pytest.hookimpl(tryfirst=True)  # after _pytest.runner
    def pytest_collection(self) -> None:
        """Prepare for test collection.

        This is invoked just before collection starts.
        """
        if not self.collection.started:
            self.time_stats.stop('Init phase')
            self.time_stats.start('Collection')
            self.collection.start()

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtestloop(self):
        """Prepare for the start of test execution.

        When pytest-xdist is not active, this is invoked after test collection,
        just before the tests start executing. When pytest-xdist is active this
        is invoked too early to be of use.

        TODO: So this can be deleted.
        """

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Handle the completion of test collection.

        When running with pytest-xdist, this is epected to occur in the
        'per-collect' background thread. Otherwise this occurs in the main
        thread.
        """
        self.time_stats.stop('Collection')
        self.time_stats.start('Execution')
        self.collection.finish()
        if not compat.run_phase_suppressed(self.config):
            self.run_phase.start()
        else:
            self.progress.stop()

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        """Handle report of zero or more collected tests.

        The report can indicate collection failure.
        """
        self.pytest_collection()
        self.collection.handle_report(report)

    def pytest_internalerror(
            self,
            excrepr: ExceptionRepr,
            excinfo: pytest.ExceptionInfo[BaseException],
        ) -> None | bool:
        """Report an internal error."""
        self.console.print(self.session.format_internal_error(excinfo))
        return True

    def pytest_warning_recorded(
            self,
            warning_message: warnings.WarningMessage,
            nodeid: str) -> None:
        """Note standard Python warning.

        Currently we are just dropping warnings.
        """

    def pytest_deselected(self, items: Sequence[pytest.Item]) -> None:
        """Not the deselction of one or more tests."""
        self.collection.deselect(items)

    def pytest_runtest_logstart(
            self, nodeid: str, location: tuple[str, int | None, str]) -> None:
        """Log the start of a test."""
        self.run_phase.log_test_start(nodeid, location)

    def pytest_runtest_logfinish(
            self, nodeid: str, location: tuple[str, int | None, str]) -> None:
        """Log the completion of a test."""
        if in_main_thread():
            self.run_phase.log_test_end(nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Log the reort for a test run."""
        self.run_phase.log_report(report)
        category, _code = interpret_report(report)
        self._save_reports(category, [report])

    def pytest_keyboard_interrupt(
            self, excinfo: pytest.ExceptionInfo[BaseException]) -> None:
        """Handle KeyboardInterrupt base exceptions.

        Note that pytest uses this for non-keyboard interruptions, such as
        errors during test collection.
        """
        self.session.handle_keyboard_interrupt(excinfo)

    def pytest_unconfigure(self) -> None:
        """Perform final action before exiting."""

    def write_sep(
            self, sep: str, title: str | None = None,
            fullwidth: int | None = None, **markup: bool,
        ) -> None:
        """Process request to write a ruled separator line.

        :sep:       Nominally a separator character, but it may be more than
                    one character.
        :title:     A title to embed within the separator line.
        :fullwidth: A value to over-ride the terminal width.
        :markup:    Markup flag values.
        """
        style_names = self.session.reporter.convert_markup(markup)
        style = ('[' + ' '.join(style_names) + ']') if style_names else ''
        self.progress.handle_output(
            self.console.print, Rule(title, style=style, characters=sep))

    def write(self, text: str | bytes, **markup: bool) -> None:
        """Process a request to write some text or bytes."""
        if not isinstance(text, str):
            text = str(text, errors='replace')
        self.progress.handle_output(
            self.console.print, self.formatter.convert_ansi_codes(text),
            end='')

    def write_line(self, line: str | bytes, **markup: bool) -> None:
        """Process a request write a line of text or bytes."""
        if not isinstance(line, str):
            line = str(line, errors='replace')
        self.progress.handle_output(
            self.console.print, self.formatter.convert_ansi_codes(line))

    # TODO: What about when progress is not running?
    # TODO: Should this start an idle progress display?
    def rewrite(self, line: str, **markup: bool) -> None:
        """Process a request to over-write the current line.

        As far as I can tell, this is used for a "poor man's progress display".
        As such, we simply add another progress task on demand.
        """
        self.progress.add_bar(
            '_extra_', bar=DescriptiveProgressBar(label='Information'))
        self.progress.update('_extra_', text=line.rstrip().replace('\n', ''))

    def _save_reports(self, category: str, reports: list[pytest.TestReport]):
        """Save one or more reports under a given category."""
        if category not in self.stats:
            self.stats[category] = []
        self.stats[category].extend(reports)
