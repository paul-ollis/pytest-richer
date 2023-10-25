"""Base helper support."""
from __future__ import annotations

import pickle
import weakref
from contextlib import suppress
from dataclasses import dataclass
from itertools import dropwhile, filterfalse, takewhile
from pathlib import Path
from typing import ClassVar, Literal, TYPE_CHECKING

import _pytest
import pluggy
import pytest
from _pytest.reports import BaseReport, CollectReport
from rich import box
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.traceback import Stack, Traceback

from . import reporting
from .progress import ProgressDisplay

if TYPE_CHECKING:
    from collections.abc import Generator

    from _pytest._code import ExceptionInfo
    from rich.traceback import Trace

FailureReportTypeName = Literal['run-phase', 'collection']

# Directory paths used to remove 'noisy' parts of test failure tracebacks.
# This is largely copied from the pyetst codebase.
pluggy_path = Path(pluggy.__file__.rstrip('oc'))
if pluggy_path.name == '__init__.py':
    pluggy_path = pluggy_path.parent
pytest_paths = [Path(pytest.__file__).parent, Path(_pytest.__file__).parent]

# This style set defined canonocal state names for tests during or after
# completion of execution.
# pylint: disable=use-dict-literal
styles = dict(
    deselected='yellow',
    failed='red',
    not_run='',
    not_started='',
    passed='green',
    running='bold breight_green',
    setup_running='',
    setup_errored='red',
    skipped='',
    teardown_running='',
    teardown_errored='yellow',
    xfailed='magenta',
    xpassed='green4',
)
std_styles = styles.copy()
std_styles.update(dict(
    skipped='yellow',
    teardown_errored='red',
    xfailed='yellow',
    xpassed='yellow',
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
# pylint: enable=use-dict-literal


class ReporterBase:                    # pylint: disable=too-few-public-methods
    """A replacement for the standard pytest terminal reporter.

    This needs to provide quite a large number of hook methods. Most of them
    simply hand off to `Helper` based objects.
    """

    def __init__(self, config: pytest.Config):
        self.config = config
        self.console: Console = Console(highlighter=None)
        self.formatter = reporting.TerminalFormatter(
            self.console,
            highlight_code=self.config.option.code_highlight == 'yes')
        self.progress = ProgressDisplay(self.console)


@dataclass
class NodeResult:
    """Captured information about a test node's results."""

    parent: Helper
    setup: pytest.TestReport | None = None
    call: pytest.TestReport | None = None
    teardown: pytest.TestReport | None = None
    started: bool = False
    expr_to_state: ClassVar[dict[str, str]] = {
        'not self.started' : 'not_started',
        'not self.setup' : 'setup_running',
        'not self.call and not self.teardown' : 'running',
        'not self.teardown' : 'teardown_running',
        'self.xfailed_report' : 'xfailed',
        'self.xpassed_report' : 'xpassed',
        'self.setup_error_report' : 'setup_errored',
        'self.teardown_error_report' : 'teardown_errored',
        'self.failed_report' : 'failed',
        'self.passed_report' : 'passed',
        'self.skipped_report' : 'skipped',
    }

    @property
    def finished(self) -> bool:
        """True when this test node has completed its run."""
        return self.teardown is not None

    @property
    def indicator(self) -> str:
        """A single (styled) character indicator of this test's state."""
        for expr, state in self.expr_to_state.items():
            # pylint: disable=eval-used
            if eval(expr):                                  # noqa: PGH001,S307
                ind = self.parent.indicator_map.get(state, '?')
                style = self.parent.style_map.get(state, '')
                if style:
                    return f'[{style}]{ind}[/]'
                else:
                    return ind
        return '?'                                           # pragma: no cover

    @staticmethod
    def _match_report(
            report: pytest.TestReport | None, outcomes: list[str],
        ) -> pytest.Report | None:
        if report and report.outcome in outcomes:
            return report
        else:
            return None

    @property
    def passed_report(self) -> pytest.TestReport | None:
        """Any passing report for this test."""
        return self._match_report(self.call, ['passed'])

    @property
    def main_error_report(self) -> pytest.TestReport | None:
        """The most significant error report for this test, if any."""
        report = self._match_report(self.call, ['failed'])
        if not report:
            report = self.setup_error_report
        if not report:
            report = self.teardown_error_report
        return report

    @property
    def failed_report(self) -> pytest.TestReport | None:
        """Any failing report for this test."""
        report = self._match_report(self.call, ['failed'])
        if not report:
            report = self.setup_error_report
        return report

    @property
    def setup_error_report(self) -> pytest.TestReport | None:
        """Any setup error report for this test."""
        return self._match_report(self.setup, ['failed'])

    @property
    def teardown_error_report(self) -> pytest.TestReport | None:
        """Any teardown error report for this test."""
        return self._match_report(self.teardown, ['failed'])

    @property
    def xfailed_report(self) -> pytest.TestReport | None:
        """Any test expected failure report."""
        call = self._match_report(self.call, ['failed', 'skipped'])
        if call and getattr(call, 'wasxfail', None) is not None:
            return call
        else:
            return None

    @property
    def xpassed_report(self) -> pytest.TestReport | None:
        """Any test unexpected passed report."""
        call = self._match_report(self.call, ['passed'])
        if call and getattr(call, 'wasxfail', None) is not None:
            return call
        else:
            return None

    @property
    def skipped_report(self) -> pytest.TestReport | None:
        """Any test skipped report."""
        setup = self._match_report(self.setup, ['skipped'])
        return setup or self._match_report(self.call, ['skipped'])


class Helper:
    """Base class for for terminal reporter helpers."""

    def __init__(self, reporter: ReporterBase):
        self.reporter = weakref.proxy(reporter)

    @property
    def config(self):
        """The PyTest configuration object."""
        return self.reporter.config

    @property
    def formatter(self):
        """The TerminalFormatter object."""
        return self.reporter.formatter

    @property
    def console(self):
        """The reporting console."""
        return self.reporter.console

    @property
    def pytest_session(self) -> pytest.Session:
        """The pytest.Session instance."""
        return self.reporter.pytest_session

    @property
    def numprocesses(self) -> int | None:
        """The number of pytest-xdist processes.

        :return:
            The value ``None`` indicates that pytest-xdist is not being used.
        """
        return self.reporter.numprocesses

    @property
    def no_summary(self) -> bool:
        """The pytest configuration no_summary flag."""
        return self.config.getoption('no_summary')

    @property
    def progress(self) -> ProgressDisplay:
        """The ProgressDisplay."""
        return self.reporter.progress

    @property
    def style_map(self) -> dict[str, str]:
        """The state to style lookup table."""
        if self.config.option.rich_std_symbols:
            return std_styles
        else:
            return styles

    @property
    def indicator_map(self) -> dict[str, str]:
        """The state to indicator character map."""
        if self.config.option.rich_std_symbols:
            return std_state_to_indicator
        else:
            return state_to_indicator

    def parse_nodeid(self, nodeid: str) -> tuple[Path, str]:
        """Parse a nodeid into its path and test_id parts."""
        path_name, _, test_id = nodeid.partition('::')
        path = Path(path_name)
        with suppress(ValueError):
            path = path.relative_to(self.config.rootpath)
        return path, test_id

    def report_failure(
            self, report: pytest.TestReport | pytest.CollectReport,
            typename: str):
        """Report failure."""

    def format_failure_report(
            self, report: pytest.TestReport | pytest.CollectReport,
            typename: str) -> Group:
        """Fromat a failure report.

        :report:   A pytest report.
        :typename: Either 'collection' or 'run-phase'.
        """
        s = []
        rich_info = getattr(report, 'rich_info', None)
        if isinstance(report, (pytest.TestReport, pytest.CollectReport)):
            if rich_info:
                trace: Trace = pickle.loads(rich_info)             # noqa: S301
                if isinstance(report, pytest.CollectReport):
                    # Only the last stack is not useful so weo remove the
                    # others. The second stack is be emptied of frames because
                    # they are all pytest or Python import machinery.
                    trace.stacks[:] = trace.stacks[-1:]
                    trace.stacks[0].frames[:] = []
                prune_stacks(trace, self.config)
                s.extend(self._format_rich_trace(trace))
        else:
            # pragma: no cover
            s.append(f'format_failure: unhandled report type {type(report)}')
        return Group(*s)

    def _format_rich_trace(self, trace: Trace) -> Traceback:
        """Format a list of Rich traces."""
        kw = {}
        level = self.config.getoption('std_tbstyle', 'long')
        if level == 'short':
            kw['extra_lines'] = 0
        if self.config.getoption('showlocals', default=False):
            kw['show_locals'] = True
        return [Traceback(trace=trace, **kw)]

    def format_sections(self, report: pytest.TestReport) -> Generator[Panel]:
        """Yield report sub-sections."""
        show_capture = self.config.option.showcapture
        for title, text in report.sections:
            if show_capture == 'no' or (
                    show_capture != 'all' and show_capture not in title):
                continue
            if isinstance(text, str):
                fixed_text: Group = self.formatter.convert_ansi_codes(text)
            else:
                fixed_text = text                           # pragma: defensive
            p = Panel(fixed_text, title=title)
            yield p

    def format_error(
            self, report: BaseReport, typename: FailureReportTypeName,
        ) -> Group:
        """Format a single pytest failure report."""
        s = []
        s.append(f'[cornflower_blue]{report.nodeid}:')
        s.append(Padding(
            self.format_failure_report(report, typename=typename),
            (0, 0, 0, 1)))
        panels = self.format_sections(report)
        paddded_panels = (Padding(panel, (0, 0, 0, 1)) for panel in panels)
        s.extend(paddded_panels)
        return Group(*s)

    def report_failures(
            self,
            failed: list[NodeResult | CollectReport],
            typename: FailureReportTypeName,
            title: str,
            style: str,
        ):
        """Report one or more failures."""
        def get_pytest_report(elem: NodeResult | CollectReport):
            if isinstance(elem, CollectReport):
                return elem
            else:
                return elem.main_error_report

        # Unlike standard pytest reporting, we always show collection when
        # --no-summary is set.
        if self.no_summary:
            failed = [r for r in failed if isinstance(r, CollectReport)]

        if failed:
            group = Group(
                *(self.format_error(get_pytest_report(report), typename)
                    for report in failed))
            panel = Panel(group, title=title, border_style=style)
            self.console.print(panel)

    @staticmethod
    def format_internal_error(excinfo: ExceptionInfo) -> Panel:
        """Format details of an internal error."""
        trace = Traceback.extract(
            exc_type=excinfo.type, exc_value=excinfo.value,
            traceback=excinfo.tb, show_locals=True)
        traceback = Traceback(trace=trace)
        return Panel(
            traceback, title='INTERNAL ERROR', subtitle='INTERNAL ERROR',
            border_style='bold red', box=box.DOUBLE_EDGE)


def prune_stacks(trace: Trace, config: pytest.Config) -> None:
    """Remove unwanted stacks entries from all stacjs in a Trace."""
    for stack in trace.stacks:
        prune_stack(stack, config)


def prune_stack(stack: Stack, config: pytest.Config) -> None:
    """Remove unwanted entries within a Stack.

    * Any pluggy frames are removed unconditionally.
    * Leading and trailing frames that appear to be any of the follwong are
      removed.

      a. No associated source file (a name like '<string>').
      b. A pytest frame.
    """
    def parents(f):
        if not no_file(f):
            return Path(f.filename).parents
        else:
            return []

    # pylint: disable=unnecessary-lambda-assignment
    is_pluggy = lambda f: pluggy_path in parents(f)                # noqa: E731
    no_file = lambda f: '<' in f.filename and '>' in f.filename    # noqa: E731
    is_pytest = lambda f: any(                                     # noqa: E731
        p for p in pytest_paths if p in parents(f))
    unwanted = lambda f: no_file(f) or is_pytest(f)                # noqa: E731
    wanted = lambda f: not unwanted(f)                             # noqa: E731

    frames = filterfalse(is_pluggy, stack.frames)
    frames = dropwhile(unwanted, frames)
    frames = takewhile(wanted, frames)
    stack.frames[:] = list(frames)

    show_locals = config.getoption('showlocals', default=False)
    for frame in stack.frames:
        if not show_locals:
            frame.locals = None
