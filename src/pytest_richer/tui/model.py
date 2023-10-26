"""Information about tests and their state."""
from __future__ import annotations
# pylint: disable=use-dict-literal

import importlib
import pickle
import time
from dataclasses import dataclass
from itertools import dropwhile, filterfalse, takewhile
from pathlib import Path
from typing import TYPE_CHECKING, final
from weakref import proxy

import _pytest
import pluggy
import pytest
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text
from rich.traceback import Traceback

import pytest_richer
from pytest_richer import protocol, theming
from pytest_richer.protocol import (
    CollectReportRepresentation, FunctionRepresentation as Function,
    ItemRepresentation as PytestItem, TestReportRepresentation)
from rich.traceback import Frame, Stack, Trace

if TYPE_CHECKING:
    from collections.abs import Iterable, Sequence

    from . import configuration
    from .test_types import TestID

dlog = pytest_richer.get_log('main')

# Directory paths used to remove 'noisy' parts of test failure tracebacks.
# This is largely copied from the pyetst codebase.
pluggy_path = Path(pluggy.__file__.rstrip('oc'))
if pluggy_path.name == '__init__.py':
    pluggy_path = pluggy_path.parent
pytest_paths = [Path(pytest.__file__).parent, Path(_pytest.__file__).parent]
collect_error_log = pytest_richer.get_log('collect-errors')
postmortem_log = pytest_richer.get_log('postmortem')

# This style set defines canonical state names for tests during or after
# completion of execution.
styles = dict(
    deselected='s-yellow',
    failed='s-red',
    not_run='',
    not_started='',
    passed='s-green',
    running='s-bold_bright_green',
    setup_running='',
    setup_errored='s-red',
    skipped='',
    teardown_running='',
    teardown_errored='s-yellow',
    xfailed='s-magenta',
    xpassed='s-green4',
)
std_styles = styles.copy()
std_styles.update(dict(
    skipped='s-yellow',
    teardown_errored='s-red',
    xfailed='s-yellow',
    xpassed='s-yellow',
))
state_to_indicator = dict(
    failed='✕',
    not_run='.',
    not_started='.',
    passed='✔',
    running='r',
    setup_running='↑',
    setup_errored='u',
    skipped='s',
    teardown_running='↓',
    teardown_errored='d',
    xfailed='f',
    xpassed='p',
)
std_state_to_indicator = state_to_indicator.copy()
std_state_to_indicator.update(dict(
    failed='F',
    passed='.',
    setup_errored='E',
    teardown_errored='E',
    xfailed='x',
    xpassed='X',
))


@final
class UNSET:
    """Indicator that a value has not yet been set."""


class TimeStatsCollector:
    """A general collector of timing statistics.

    The usage is simple. Create a ``TimeStatsCollector`` instance then call
    `start(name)` to start a timer for a given name and then stop(name) to stop
    the timer for the given name.

    The ``TimeStatsCollector`` can be iterated to get simple tuples of ``(name,
    elapsed-time)``.
    """

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
        """Iterate over the timing stats."""
        for name, (start, stop) in self.stats.items():
            if stop is not None:
                yield name, stop - start


@dataclass(eq=False)
class NodeResult:
    """Captured information about a test and its status.

    @parent:   The parent `Helper`.
    @item:     The PytestItem or None.
    @setup:    The TestReportRepresentation for the setup phase.
    @call:     The TestReportRepresentation for the execution phase.
    @teardown: The TestReportRepresentation for the teardown phase.
    @started:  False if the test has not yet started running.
    @parked:   Set if this test is parked (not scheduled to be run).
    """

    parent: TestState
    item: PytestItem | None = None
    _setup: TestReportRepresentation | pytest.Report | None = None
    _call: TestReportRepresentation | pytest.Report | None = None
    _teardown: TestReportRepresentation | pytest.Report | None = None
    started: bool = False
    parked: bool = False
    _clean_setup: CollectReportRepresentation | type(UNSET) = UNSET
    _clean_call: CollectReportRepresentation | type(UNSET) = UNSET
    _clean_teardown: CollectReportRepresentation | type(UNSET) = UNSET
    _cached_state: str | None = None

    @property
    def nodeid(self) -> str:
        """The test's nodeid string."""
        return self.item.nodeid

    @property
    def setup(self) -> TestReportRepresentation | None:
        """The test setup up report if it exists."""
        if self._clean_setup is UNSET:
            if self._setup is None:
                self._clean_setup = None
            else:
                self._clean_setup = protocol.denodify_report(self._setup)
        return self._clean_setup

    @property
    def teardown(self) -> TestReportRepresentation | None:
        """The test teardown up report if it exists."""
        if self._clean_teardown is UNSET:
            if self._teardown is None:
                self._clean_teardown = None
            else:
                self._clean_teardown = protocol.denodify_report(self._teardown)
        return self._clean_teardown

    @property
    def call(self) -> TestReportRepresentation | None:
        """The test call up report if it exists."""
        if self._clean_call is UNSET:
            if self._call is None:
                self._clean_call = None
            else:
                self._clean_call = protocol.denodify_report(self._call)
        return self._clean_call

    @setup.setter
    def setup(self, value: TestReportRepresentation) -> None:
        self._setup = value
        self._clean_setup = UNSET
        self._cached_state = None

    @teardown.setter
    def teardown(self, value: TestReportRepresentation) -> None:
        self._teardown = value
        self._clean_teardown = UNSET
        self._cached_state = None

    @call.setter
    def call(self, value: TestReportRepresentation) -> None:
        self._call = value
        self._clean_call = UNSET
        self._cached_state = None

    @property
    def path(self) -> Path:
        """The test script Path for this pytest node."""
        return self.item.path

    @property
    def finished(self) -> bool:
        """True when this test node has completed its run."""
        return self.teardown is not None

    @property
    def state(self) -> str:
        """A simple string describing this test's state."""
        if self._cached_state is None:
            if not self.started:
                self._cached_state = 'not_started'
            elif not self.setup:
                self._cached_state = 'setup_running'
            elif not self.call and not self.teardown:
                self._cached_state = 'running'
            elif not self.teardown:
                self._cached_state = 'teardown_running'
            elif self.xfailed_report:
                self._cached_state = 'xfailed'
            elif self.xpassed_report:
                self._cached_state = 'xpassed'
            elif self.setup_error_report:
                self._cached_state = 'setup_errored'
            elif self.teardown_error_report:
                self._cached_state = 'teardown_errored'
            elif self.failed_report:
                self._cached_state = 'failed'
            elif self.passed_report:
                self._cached_state = 'passed'
            elif self.skipped_report:
                self._cached_state = 'skipped'
            else:
                self._cached_state = ''                                            # pragma: no cover
        return self._cached_state

    def style_indicator_run(self, run: list[str]) -> str:
        style = self.parent.style_map.get(self.state, '', disabled=self.parked)
        text = ''.join(run)
        if style:
            return f'[{style}]{text}[/]'
        else:
            return text

    @property
    def indicator(self) -> str:
        """A single (styled) character indicator of this test's state."""
        state = self.state
        ind = self.parent.indicator_map.get(state, '?')
        style = self.parent.style_map.get(state, '', disabled=self.parked)
        if self.nodeid == 'simple-demo-tests/test_editing.py::test_1':
            print("IND", (style, ind))
        if style:
            return f'[{style}]{ind}[/]'
        else:
            return ind
        return '?'                                           # pragma: no cover

    @property
    def plain_indicator(self) -> str:
        """A single (unstyled) character indicator of this test's state."""
        state = self.state
        return self.parent.indicator_map.get(state, '?')

    @staticmethod
    def _match_report(
            report: TestReportRepresentation | None, outcomes: list[str],
        ) -> pytest.Report | None:
        """Match a report to one of a set of outcomes.

        :report:   The report to compare agains the outcomes.
        :outcomes: A list of outcome names.
        :return:
            The report if its outcome is in the provided list. Otherwise
            ``None``.
        """
        if report and report.outcome in outcomes:
            return report
        else:
            return None

    @property
    def passed_report(self) -> TestReportRepresentation | None:
        """Any passing report for this test."""
        return self._match_report(self.call, ['passed'])

    @property
    def main_error_report(self) -> TestReportRepresentation | None:
        """The most significant error report for this test, if any."""
        report = self._match_report(self.call, ['failed'])
        if not report:
            report = self.setup_error_report
        if not report:
            report = self.teardown_error_report
        return report

    @property
    def failed_report(self) -> TestReportRepresentation | None:
        """Any failing report for this test."""
        report = self._match_report(self.call, ['failed'])
        if not report:
            report = self.setup_error_report
        return report

    @property
    def setup_error_report(self) -> TestReportRepresentation | None:
        """Any setup error report for this test."""
        return self._match_report(self.setup, ['failed'])

    @property
    def teardown_error_report(self) -> TestReportRepresentation | None:
        """Any teardown error report for this test."""
        return self._match_report(self.teardown, ['failed'])

    @property
    def xfailed_report(self) -> TestReportRepresentation | None:
        """Any test expected failure report."""
        call = self._match_report(self.call, ['failed', 'skipped'])
        if call and getattr(call, 'wasxfail', None) is not None:
            return call
        else:
            return None

    @property
    def xpassed_report(self) -> TestReportRepresentation | None:
        """Any test unexpected passed report."""
        call = self._match_report(self.call, ['passed'])
        if call and getattr(call, 'wasxfail', None) is not None:
            return call
        else:
            return None

    @property
    def skipped_report(self) -> TestReportRepresentation | None:
        """Any test skipped report."""
        setup = self._match_report(self.setup, ['skipped'])
        return setup or self._match_report(self.call, ['skipped'])

    def park(self):
        """Park test for an up-coming test run."""
        self.parked = True

    def unpark(self):
        """Unpark this test for after a test run."""
        print("UNPARK", self.nodeid)
        self.parked = False

    def reset(self):
        """Reset this ready to be re-run."""
        self.parked = False
        self.started = False
        self.setup = self.call = self.teardown = None
        self._cached_state = None

    # TODO: Probably not used.
    def get_to_sensible_state(self, nodeid) -> bool:
        """Get this to a sensible state, if it has been started.

        This is used to make sure this `NodeResult` provides sensible looking
        information in the event that a pytest run stopped before providing all
        expected reports.
        """
        def passed(nodeid: str, when: str) -> TestReportRepresentation:
            return pytest.TestReportRepresentation(
                nodeid=nodeid, when=when, location=('', None, ''),
                keywords={}, outcome='passed', longrepr=None)

        if self.started:
            if self.teardown:
                # All test reports have been applied.
                return False
            elif self.setup and self.call:
                # We assume that teardown ran OK.
                self.teardown = passed(nodeid=nodeid, when='teardown')
                return True
            elif self.setup:
                if self.setup.outcome == 'passed':
                    # Pretend this never started.
                    self.setup = None
                    self.started = False
                    return True
                else:
                    # We assume that teardown ran OK.
                    self.teardown = passed(nodeid=nodeid, when='teardown')
                    return True
        return False

    def format_failure(                    # pylint: disable=too-many-arguments
            self,
            *,
            show_locals: bool = False,
            show_simple_stack: bool = False,
            n_context: int = 3,
            max_string: int = 40,
            max_length: int = 10,
            dark_bg: bool,
        ) -> Group | None:
        """Format the failure report for this result, if any."""
        report = self.main_error_report
        if not report:
            return None

        print(f'format_failure: {report.nodeid=}', file=postmortem_log)
        s = []
        s.append(f'[cornflower_blue]{report.nodeid}:')
        s.append(Padding(
            format_failure_report(
                report,
                n_context=n_context,
                show_locals=show_locals,
                show_simple_stack=show_simple_stack,
                max_length=max_length,
                max_string=max_string,
                dark_bg=dark_bg,
            ),
            (0, 0, 0, 1)))
        return Group(*s)

    def format_section(self, name: str) -> Panel | None:
        """Format a specific section."""
        # TODO: Should this get text from all reports?
        report = self.main_error_report
        if not report:
            return None

        rep: Text | str
        for title, rep in report.sections:
            if name not in title:
                continue
            text = Text.from_ansi(rep) if isinstance(rep, str) else rep
            return Panel(text, title=title)
        return None

    def as_rich_text(self) -> Text:
        """Format this `NodeResult` as a Rich.Text instance."""
        return Text.assemble(
            f'{self.__class__.__name__}[', Text.from_markup(self.indicator),
            ']')


@dataclass(eq=False)
class CollectionFailureResult:
    """Captured information about a test collection failure.

    @parent:   The parent `Helper`.
    @report:   The CollectReportRepresentation.
    @cleaned:  This is set when the CollectReportRepresentation has been
               cleaned up as required for generating rich stack traces.
    """

    parent: TestState
    _report: CollectReportRepresentation
    _clean_report: CollectReportRepresentation | type(UNSET) = UNSET

    @property
    def nodeid(self) -> str:
        """The reports's nodeid string."""
        return self._report.nodeid

    @property
    def report(self) -> CollectReportRepresentation | None:
        """The test report up report if it exists."""
        if self._clean_report is UNSET:
            if self._report is None:
                self._clean_report = None
            else:
                self._clean_report = protocol.denodify_report(self._report)
        return self._clean_report

    def format_failure(                    # pylint: disable=too-many-arguments
            self,
            *,
            show_locals: bool = False,
            show_simple_stack: bool = False,
            n_context: int = 3,
            max_string: int = 40,
            max_length: int = 10,
            dark_bg: bool,
        ) -> Group | None:
        """Format the failure report for this result, if any."""
        report = self.report
        if not report:
            return None

        print(f'format_failure: {report.nodeid=}', file=postmortem_log)
        s = []
        s.append(f'[cornflower_blue]{report.nodeid}:')
        s.append(Padding(
            format_failure_report(
                report,
                n_context=n_context,
                show_locals=show_locals,
                show_simple_stack=show_simple_stack,
                max_length=max_length,
                max_string=max_string,
                dark_bg=dark_bg,
            ),
            (0, 0, 0, 1)))
        return Group(*s)

    def format_section(self, name: str) -> Panel | None:
        """Format a specific section."""
        # TODO: Should this get text from all reports?
        report = self.report
        if not report:
            return None

        rep: Text | str
        for title, rep in report.sections:
            if name not in title:
                continue
            text = Text.from_ansi(rep) if isinstance(rep, str) else rep
            return Panel(text, title=title)
        return None


class TestState:                      # pylint: disable=too-many-public-methods
    """Storage of the state of all known tests.

    @items:
        A mapping from nodeid to NodeResult instances representing all the
        successfully collected tests.
    @collect_failures:
        A mapping from nodeid to CollectionFailureResult instances.
    @collect_skipped:
        A mapping from nodeid to CollectReportRepresentation for tests that
        were skipped during collection.
    @deselected:
        A set of nodeid values for test that were deselected during test
        collection.
    """

    def __init__(self, config: configuration.Config):
        self.config = config
        self.items: dict[TestID, NodeResult]
        self.collect_failures: dict[TestID, CollectionFailureResult]
        self.collect_skipped: dict[TestID, CollectReportRepresentation]
        self.deselected: set[str]
        self._processed_collect_reports: set[TestID]
        self._reset()

        # Track a count of how many items (tests) have finished. This is an
        # optimization. The value can be calculated as::
        #
        #    len([res for res in self.items.values() if res.finished])
        #
        # But, profiling showed that has a serious CPU load.
        self.finished_count: int = 0

    def _reset(self) -> None:
        """Reset stateful content."""
        self.items = {}
        self.collect_failures = {}
        self.collect_skipped = {}
        self.deselected = set()
        self._processed_collect_reports = set()

    @property
    def indicator_map(self) -> dict[str, str]:
        """The state to indicator character map."""
        if self.config.lookup_item('overview-std_symbols').value:
            return std_state_to_indicator
        else:
            return state_to_indicator

    @property
    def style_map(self) -> dict[str, str]:
        """The state to style lookup table."""
        dark_bg = self.config.lookup_item('run-dark_mode').value
        if self.config.lookup_item('overview-std_symbols').value:
            return theming.Theme(dark_bg=dark_bg, styles=std_styles)
        else:
            return theming.Theme(dark_bg=dark_bg, styles=styles)

    #
    # Test collection management.
    #
    def prepare_for_run(self, selection: set[TestID]) -> None:
        """Preperare to re-run some or all tests."""
        if not selection:
            self._reset()
        else:
            self._processed_collect_reports.clear()

    def park_and_reset_stored_results(self, selection: set[TestID]) -> None:
        for result in self.query_results([]):
            if result.nodeid in selection:
                result.reset()
            else:
                result.park()

    #
    # Test collection processing.
    #
    def prepare_for_test_collection(self) -> None:
        """Prepare for test collection."""
        self._processed_collect_reports.clear()

    def add_collected_tests(
            self, report: CollectReportRepresentation) -> tuple[bool, bool]:
        """Add tests from a pytest collection report.

        When pytest is running with pytest-xdist enabled, multiple reports for
        the same test may be received. This method performs deduplication.

        :report:
            The is a protocol.CollectReportRepresentation object. The important
            attributes are:

            result
                A list of PytestItem and pytest.Collector instances. The
                PytestItem instances represent actual tests.
            outcome
                A string that can be 'passed', 'skipped or 'failed'.
            longrepr
                If the outcome 'skipped' then this is as a tuple of:

                    path_name: str, lineno: int, message: str

                If outcome == 'failed', this is either an ExceptionChainRepr or
                a simple CollectErrorRepr. The latter simply contains a
                formatted exception in its ``longrepr`` string attribute.
        :return:
            A tuple of (added_tests, collect_failed). The ``added_tests`` flag
            indicates that one or more new test got added to the collection.
            The ``collect_failed`` indicates that a new collection failure
            occurred.
        """
        items = []
        added, failed = False, False
        if not self._collect_report_already_seen(report):
            if report.outcome == 'failed':
                result = CollectionFailureResult(proxy(self), report)
                self.collect_failures[report.nodeid] = result
                failed = True
            elif report.outcome == 'skipped':
                self.collect_skipped[report.nodeid] = report
            else:
                items = [x for x in report.result if isinstance(x, Function)]
                for item in items:
                    self.items[item.nodeid] = NodeResult(proxy(self), item)
                added = True
        return added, failed

    def deselect_tests(self, items: list[PytestItem]) -> None:
        """Note the deselection of one or more tests."""
        for item in items:
            self.deselected.add(item.nodeid)
            self.items.pop(item.nodeid)

    def format_collection_progress(self, *, final: bool=False) -> str:
        """Format a Rich string describing collection progress."""
        n_deselected = len(self.deselected)
        n_failed = len(self.collect_failures)
        n_skipped = len(self.collect_skipped)
        n_selected = len(self.items) - n_deselected
        leader = 'complete' if final else 'running'
        s = [f'[cyan][bold]{leader}[/]: [/]selected=[green]{n_selected}[/]']
        if n_skipped:
            s.append(f'skipped=[blue]{n_skipped}[/]')
        if n_deselected:
            s.append(f'deselected=[yellow]{n_deselected}[/]')
        if n_failed:
            s.append(f'failed=[red]{n_failed}[/]')
        return ' '.join(s)

    def _collect_report_already_seen(self, report) -> bool:
        """Check if a report has already been seen.

        :return: True if the report has already been seen.
        """
        seen = report.nodeid in self._processed_collect_reports
        self._processed_collect_reports.add(report.nodeid)
        return seen

    #
    # Test execution phase processing.
    #
    def start_test(self, nodeid: str) -> NodeResult | None:
        """Mark a test as started."""
        if result := self.items.get(nodeid):
            result.started = True
        return result

    def store_test_report(
            self, report: TestReportRepresentation) -> NodeResult | None:
        """Store a pytest report for a given test.

        This is invoked multiple times for a given test. The ``report.when``
        attribute identifies the test's phase; 'setup', 'call' and 'teardown'.
        This is always invoked for the setup and teardown phases, but is
        omitted for the call phase if the setup phase failed.

        :return:
            The `NodeResult` where the report was stored or ``None`` if the
            nodeid did not map to a known test.
        """
        if result := self.items.get(report.nodeid):
            if report.when == 'teardown' and not result.finished:
                self.finished_count += 1
            setattr(result, report.when, report)
            if 'test_focus.py' in result.nodeid:
                print(
                    f'Store: {report.when} {report.outcome} {report.nodeid}',
                    file=dlog)
        return result

    def end_test(self, nodeid: str) -> NodeResult | None:
        """Perform actions for when a test has ended."""
        return self.items.get(nodeid)

    #
    # Status queries.
    #
    def query_results(
            self, test_ids: Sequence[TestID]) -> Iterable[NodeResult]:
        """Get a list of results for a given selection of nodeids.

        If test_ids is empty, all available NodeResult instances are selected.

        :return:
            In iterable of NodeResult instances.
        """
        if not test_ids:
            return self.items.values()
        else:
            return (self.items[test_id] for test_id in test_ids)

    def completion_counts(self) -> [int, int]:
        """Count how many tests have finished and total test count.

        :return: A tuple of N finished tests, total test count.
        """
        return self.finished_count, len(self.items)

    @property
    def passed(self) -> Sequence[NodeResult]:
        """The results for the passing tests."""
        return [res for res in self.items.values()
            if res.passed_report and not res.xpassed_report
                and res.finished]

    @property
    def failed(self) -> Sequence[NodeResult]:
        """The results for the tests that failed unexpectedly."""
        return [res for res in self.items.values()
            if res.failed_report and not res.xfailed_report
                and not res.setup_error_report]

    @property
    def setup_errored(self) -> Sequence[NodeResult]:
        """The results for the tests failed during setup."""
        return [res for res in self.items.values()
            if res.setup_error_report]

    @property
    def teardown_errored(self) -> Sequence[NodeResult]:
        """The results for the tests that failed during teardown."""
        return [res for res in self.items.values()
            if res.teardown_error_report]

    @property
    def xfailed(self) -> Sequence[NodeResult]:
        """The results for the tests that failed as expected."""
        return [res for res in self.items.values()
            if res.xfailed_report]

    @property
    def xpassed(self) -> Sequence[NodeResult]:
        """The results for the tests that failed as expected."""
        return [res for res in self.items.values()
            if res.xpassed_report]

    @property
    def skipped(self) -> Sequence[NodeResult]:
        """The results for the tests that were skipped."""
        return [res for res in self.items.values()
            if res.skipped_report and not res.xfailed_report]

    @property
    def not_run(self) -> Sequence[NodeResult]:
        """The results for tests that did not run (no teardown)."""
        return [res for res in self.items.values()
            if not res.teardown]

    #
    # Reporting support.
    #
    def format_summary(
            self, *, full: bool = False,
            time_stats: TimeStatsCollector | None = None) -> Text:
        """Format the general summary of the test run."""
        # pylint: disable=too-many-locals
        style_map = self.style_map
        def_style = style_map.get('default')
        lab_width = 22
        type_names = (
            'passed', 'failed', 'xfailed', 'xpassed', 'not_run', 'skipped',
            'setup_errored', 'teardown_errored')
        name_to_label = {
            'xfailed': 'Expected failures',
            'xpassed': 'Unexpected passes',
            'setup_errored': 'Setup errors',
            'teardown_errored': 'Teardown errors',
        }

        def styled(name: str) -> str:
            return style_map.get(name, '')

        def label(*parts: str | Text) -> Text:
            lab = Text.assemble(*parts, ':')
            if lab.cell_len < lab_width:
                lab = Text.assemble(lab, ' ' * (lab_width - lab.cell_len))
            return lab

        def number(n: int, style: str = def_style) -> Text:
            return Text.assemble((f'{n:>4}', style))

        parts = []
        add = parts.append

        add(label('TOTAL tests'))
        add(number(len(self.items) + len(self.deselected)))
        if self.deselected:
            add('\n')
            add(label('Deselected'))
            add(number(len(self.deselected), styled('deselected')))

        if full:
            for state in type_names:
                if seq := getattr(self, state):
                    def_label = state.capitalize().replace('_', ' ')
                    s = [name_to_label.get(state, def_label)]
                    if indicator := self.indicator_map.get(state, '?'):
                        style = style_map.get(state)
                        s.append(Text.from_markup(
                            f' ([{style}]{indicator}[/])'))
                    add('\n')
                    add(label(Text.assemble(*s)))
                    add(number(len(seq), styled(state)))

            if time_stats:
                tot = 0.0
                for name, elapsed in time_stats:
                    add('\n')
                    add(label(Text.assemble(name)))
                    add(f'{elapsed:8.3f}s')
                    tot += elapsed
                add('\n')
                add(label(Text.assemble('Overall')))
                add(f'{tot:8.3f}s')

        return Text.assemble(*parts)


def format_failure_report(             # pylint: disable=too-many-arguments
        report: TestReportRepresentation | CollectReportRepresentation,
        *,
        show_locals: bool = False,
        show_simple_stack: bool = False,
        max_string: int = 40,
        max_length: int = 10,
        n_context: int = 3,
        dark_bg: bool,
    ) -> Group:
    """Format a failure report.

    :report:       A pytest report.
    :show_locals:  True is local variables should be shown.
    :max_string:   The maximum length for displayed strings.
    :max_length:   The maximum number of list, dict, *etc.* entries to show.
    :n_context:    The number of preceding and following lines of context to
                   show for each frame.
    :dark_bg:      When True, format for a dark background.
    """
    s = []
    report_types = (
        TestReportRepresentation, pytest.CollectReport,
        protocol.TestReportRepresentation,
        protocol.CollectReportRepresentation,
    )
    print(f'>>> {type(report)}', file=postmortem_log)
    if isinstance(report, report_types):
        rich_traceback: Trace | None = getattr(report, 'rich_traceback', None)
        if rich_traceback:
            trace: Trace = _copy_trace(rich_traceback)
            if isinstance(report, CollectReportRepresentation):
                # Only the last stack is useful so we remove the others.
                trace.stacks[:] = trace.stacks[-1:]
            prune_stacks(
                trace, show_locals=show_locals, simple_stack=show_simple_stack)
            s.extend(_format_rich_trace(
                trace,
                n_context=n_context,
                show_locals=show_locals,
                max_length=max_length,
                max_string=max_string,
                dark_bg=dark_bg,
            ))
    else:
        # pragma: no cover
        s.append(f'format_failure: unhandled report type {type(report)}')
    return Group(*s)


def _copy_trace(trace: Trace) -> Trace:
    """Create a copy of a Trace object.

    This is about 100 time faster that using copy.deepcopy().
    """
    def copy_frame(frame: Frame) -> Frame:
        return Frame(
            frame.filename, frame.lineno, frame.name, frame.line,
            frame.locals.copy())

    def copy_stack(stack: Stack) -> Stack:
        new_frames = [copy_frame(frame) for frame in stack.frames]
        return Stack(
            stack.exc_type, stack.exc_value, stack.syntax_error,
            stack.is_cause, new_frames)

    new_stacks = [copy_stack(stack) for stack in trace.stacks]
    return Trace(new_stacks)


def _format_rich_trace(                    # pylint: disable=too-many-arguments
        trace: Trace, *,
        show_locals: bool = False,
        n_context: int = 3,
        max_string: int = 40,
        max_length: int = 10,
        dark_bg: bool,
    ) -> Traceback:
    """Format a list of Rich traces."""
    kw = {}
    kw['extra_lines'] = n_context
    kw['show_locals'] = show_locals
    kw['locals_max_length'] = max_length
    kw['locals_max_string'] = max_string
    kw['theme'] = theming.traceback_themes[dark_bg]
    tb = Traceback(trace=trace, **kw)
    return [tb]


def prune_stacks(
        trace: Trace, *, show_locals: bool = False, simple_stack: bool = False,
    ) -> None:
    """Remove unwanted stacks entries from all stacks in a Trace."""
    for stack in trace.stacks:
        prune_stack(stack, show_locals=show_locals, simple_stack=simple_stack)


def prune_stack(
        stack: Stack, *, show_locals: bool = False, simple_stack: bool = False,
    ) -> None:
    """Remove unwanted entries within a Stack.

    * Any pluggy frames are removed unconditionally.
    * Leading and trailing frames that appear to be any of the follwong are
      removed.

      a. No associated source file (a name like '<string>').
      b. A pytest frame.
    """
    def no_file(frame) -> bool:
        return '<' in frame.filename and '>' in frame.filename

    def parents(frame):
        if not no_file(frame):
            return Path(frame.filename).resolve().parents
        else:
            return []

    def is_framework_frame(frame) -> bool:
        if any(p for p in pytest_paths if p in parents(frame)):
            return True
        return False

    def is_leading_framework_frame(frame) -> bool:
        if is_framework_frame(frame):
            return True
        if pluggy_path in parents(frame):
            return True
        if '<' in frame.filename and '>' in frame.filename:
            return True
        if frame.filename == importlib.__file__:
            return True
        return False

    def is_pluggy_frame(frame) -> bool:
        return pluggy_path in parents(frame)

    if simple_stack:
        frames = dropwhile(is_leading_framework_frame, stack.frames)
        frames = filterfalse(is_pluggy_frame, frames)
        frames = takewhile(lambda f: not is_framework_frame(f), frames)
        stack.frames[:] = list(frames)
    for frame in stack.frames:
        if not show_locals:
            frame.locals = {}
