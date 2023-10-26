"""Main TUI application code."""
from __future__ import annotations

import asyncio
import cProfile
import functools
import importlib.util
import linecache
import os
import pstats
import subprocess
from pathlib import Path
from typing import ClassVar, TYPE_CHECKING, cast
from weakref import WeakSet, proxy

from textual import on, walk
from textual.app import App, message_hook_context_var
from textual.binding import Binding
from textual.events import Key, Message, Mount, MouseEvent, Idle
from textual.reactive import reactive
from textual.widgets import (
    Button, Checkbox, Footer, Header, Input, ListView, TabbedContent, Tree)

import pytest_richer
from pytest_richer import protocol
from pytest_richer.tui import configuration, events
from pytest_richer.tui.collection_control import CollectionController
from pytest_richer.tui.config_control import ConfigController
from pytest_richer.tui.configuration import AutoRunMode
from pytest_richer.tui.control import Controller
from pytest_richer.tui.diag_control import DiagnoticsController
from pytest_richer.tui.model import NodeResult, TestState, TimeStatsCollector
from pytest_richer.tui.overview_control import OverviewController
from pytest_richer.tui.postmortem_control import PostMortemController
from pytest_richer.tui.test_progress import (
    DescriptiveProgressBar, GraphBar, PercentageProgressBar, ProgressDisplay,
    SlottedProgressBar, TestProgressMapper, indicator_as_rich_text)
from pytest_richer.tui.test_types import PytestWarning
from pytest_richer.tui.widgets import HIDTicker
if importlib.util.find_spec('watchdog'):
    from pytest_richer.tui import file_watching
    from pytest_richer.tui.watch_control import FileWatchController
else:
    file_watching = None

if TYPE_CHECKING:
    import argparse
    import warnings
    from collections.abc import Iterator
    from typing import Any, Callable, Literal

    import pytest
    from textual import Timer
    from textual.geometry import Region
    from textual.widget import Widget

    from pytest_richer.protocol import (
        ConfigRepresentation, SessionRepresentation)
    from pytest_richer.tui.test_types import TestID

ROOT = Path.cwd()
NL = 10
dlog = pytest_richer.get_log('main')
proto_log = pytest_richer.get_log('protocol')
collect_error_log = pytest_richer.get_log('collect-errors')


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


class BufferedWriter:
    """A writer that buffers partial lines for a limited time."""

    def __init__(self, app: App, flush: Callable[[str], []]):
        self.buf: str = ''
        self.flush = flush
        self.app = proxy(app)
        self.timer: Timer | None = None

    def append(self, s: str):
        """Append a string to the buffer and flush complete lines."""
        if s and s[-1] == '\n':
            self.buf, text = '', self.buf + s
        else:
            leader, _, rem = s.rpartition('\n')
            if leader:
                self.buf, text = rem, self.buf + leader
            else:
                self.buf, text = self.buf + s, ''
        for line in text.splitlines():
            self.flush(line)

        if self.timer is None:
            self.timer = self.app.set_interval(0.5, self.flush_partial)
        self.timer.reset()

    def flush_partial(self):
        """Flush any partial line."""
        if self.buf:
            self.flush(self.buf)
            self.buf = ''


class MainControl(Controller):
    """The main part of the application controller."""

    # pylint: disable=too-many-instance-attributes
    non_panel = True

    def __init__(
            self,
            app: PytestApp,
        ):
        super().__init__(app=app)
        self.test_state: TestState = TestState(self.config)
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.failing_tests: dict[str: NodeResult] = {}
        self.running_tests: set[str] = set()
        self.progress_mapper: TestProgressMapper | None = None
        self.phase: str = ''
        self.held_back_proto_calls: list[Callable[[], None]] = []
        self._message_handlers: WeakSet = WeakSet()
        self.run_config: ConfigRepresentation | None = None
        self.numprocesses = 1
        self.stats = TimeStatsCollector()
        self.profiler: cProfile.Profile | None = None

    @property
    def args(self) -> argparse.Namespace:
        """The parsed command line arguments."""
        return self.app.args

    @property
    @functools.cache
    def progress(self) -> ProgressDisplay:
        """The progress display widget."""
        return self.app.query_one('#progress_display')

    @property
    def pytest_is_running(self):
        """True when a subprocess pytest run is in progress."""
        return bool(self.proc)

    @property
    def collection_failures_occurred(self) -> bool:
        """True if any collection failures occurred."""
        return bool(self.test_state.collect_failures)

    @property
    def test_failures_occurred(self) -> bool:
        """True if any tests failed unexpected.

        This is also true when any test setup occurred.
        """
        bad_test = bool(self.test_state.failed)
        bad_setup = bool(self.test_state.setup_errored)
        return bad_test or bad_setup

    #
    # Execution of test runs; running pytest in a subprocess.
    #
    async def start_pytest(self, selection: set[str] = frozenset()):
        """Start a new pytest run, unless one is already in progress."""
        if self.proc:
            return

        self.app.action_show_tab('overview_pane')
        linecache.clearcache()
        self.test_state.prepare_for_run(selection)
        args = ['pytest', '--subprocess-mode', '--dist=loadgroup']
        if self.config.pytest.has_xdist:
            if selection:
                self.numprocesses = min(28, len(selection))
            else:
                self.numprocesses = 40
            if self.numprocesses > 1:
                args.append(f'-n{self.numprocesses}')
            # '--maxfail=6',
        else:
            self.numprocesses = 1

        if selection:
            args.extend(selection)
            self.test_state.park_and_reset_stored_results(selection)
            for result in self.test_state.query_results([]):
                self._update_test_progress(result)
        else:
            print(f'PAUL: MainControl.start_pytest: {self.args.profile=}')
            self.progress_mapper = None
            self.progress.reset()
            args.append(self.config.lookup_item('run-test_dir').value)
        self.progress.redraw()
        self.app.dispatch_operation('reset')
        print(f'Run: {args}')
        print(f'Run: {args}', file=dlog)
        if self.args.profile:
            self.profiler = cProfile.Profile()
            self.profiler.enable()
        self.proc = await asyncio.create_subprocess_exec(
            *args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.reader = asyncio.create_task(self.process_test_run_output())
        self.app.diag.monitor_task(self.reader)
        self.app.set_disabled()

    async def process_test_run_output(self):
        """Process output from the running pytest.

        This is runs as a task that reads and interprets line-based messages
        from a subprocess.
        """
        unhandled_messages: set[str] = set()
        partline: bytes = b''
        while True:
            block = await self.proc.stdout.read(1024)
            if block:
                lines: list[str] = []
                parts = block.splitlines(keepends=True)
                for i, line in enumerate(parts):
                    print(f'BLine[{i}]: {line}', file=proto_log)
                part = parts.pop(0)
                if part[-1] == NL:
                    lines.append(partline + part)
                    partline = b''
                    if parts and parts[-1][-1] != NL:
                        partline = parts.pop()
                else:
                    partline += part
                lines.extend(parts)
                self._process_lines(lines, unhandled_messages)
            else:
                # End of file on input pipe. The pytest process has stopped.
                break

        ret = await self.proc.wait()
        self.proc = None
        self.app.set_disabled()
        if self.args.profile:
            self.profiler.disable()
            self.profiler.create_stats()
            st = pstats.Stats(self.profiler)
            st.dump_stats('prof.bin')

    def _process_lines(
            self, lines: list[bytes], unhandled_messages: set[str]) -> None:
        """Process new lines received from the child pytest process.

        The lines fed into this method are a mixture of raw pytest output and
        encoded information.linecach lines start with '<<--RICH-PIPE-->>:'. This magic string is
        followed by the name of a method and any pickled arguments.

        :lines:
            A list of lines each as a bytes instance.
        :unhandled_messages:
            A set of previously unhandled messages. This is used to log
            unhandled messages just once.
        """
        for bline in lines:
            line = bline.decode('utf-8', errors='ignore').rstrip()
            parts = line.split()
            leader = ''
            if parts:
                leader, *args = line.split()
            if leader == '<<--RICH-PIPE-->>:' and args:
                method_name, args = args[0], args[1:]
                is_report = 'report' in method_name
                is_test_method = method_name.endswith('_test')
                if not (is_report or is_test_method):
                    print(f'PIPE: {method_name}', file=dlog)
                # print(f'Dispatch {method_name}(...)', file=dlog)
                if (not self.dispatch_pytest_message(method_name, args)
                        and method_name not in unhandled_messages):
                    print(f'Unhandled message: {method_name}', file=dlog)
                    unhandled_messages.add(method_name)
                else:
                    args_str = ', '.join(args)
            else:
                self.app.write_line(line)

    def dispatch_pytest_message(
            self, method_name: str, args: list[str]) -> bool:
        """Dispatch based on a pytest message.

        :return: True if a handler was found.
        """
        handled = False
        dec_args = None
        for handler_obj in self._iter_handlers():
            method = getattr(handler_obj, method_name, None)
            if method:
                if dec_args is None:
                    dec_args = [protocol.decode(arg) for arg in args]
                method(*dec_args)
                handled = True
        return handled

    def add_pytest_message_handler(self, obj: Any) -> None:      # noqa: ANN401
        """Add an object as a handler for one or more pytest messages."""
        self._message_handlers.add(obj)

    def _iter_handlers(self) -> Iterator:
        yield self
        yield from self._message_handlers

    @on(events.WatchedFilesChanged)
    async def handle_files_changed(self) -> None:
        """React to a watched file change event."""
        auto_run_mode = self.app.query_one('#auto_run_mode').value
        await self.start_pytest_for_selected_mode(auto_run_mode)

    async def start_pytest_for_selected_mode(self, mode: AutoRunMode) -> None:
        """Perform action for the pressed button."""
        if mode == AutoRunMode.ALL_FAILING:
            await self.start_pytest(self.app.control.failing_tests)
        elif mode== AutoRunMode.SELECTED_FAILING:
            failing_tests = self.app.call_function(
                'selected_failing_nodeid_set') or set()
            await self.start_pytest(failing_tests)
        elif mode== AutoRunMode.ALL:
            await self.start_pytest()

    #
    # Handlers for messages from the pytest subprocess.
    #

    # Overall test session management.
    def proto_session_start(self, _session: SessionRepresentation) -> None:
        """React to the start of a test session."""
        self.stats = TimeStatsCollector()
        self.stats.start('Init phase')

    def proto_runtestloop(self) -> None:
        """React to the start of the test execution loop."""

    def proto_session_end(self, _exit_stats: int) -> None:
        """React to the end of a test session."""
        self.stats.stop('Execution')
        self._update_run_summary(include_timings=True)
        self.app.post_message(events.EndTestRunMessage())

    def proto_unconfigure(self) -> None:
        """React to the final unconfigure/shutdown."""

    # Collection phase.
    def proto_test_collection_start(self) -> None:
        """Prepare for test collection."""
        self.phase = 'collection'
        self.stats.stop('Init phase')
        self.stats.start('Collection')
        self.test_state.prepare_for_test_collection()
        self.progress.add_bar(
            'collection', bar=DescriptiveProgressBar(label='Collection'))

    def proto_test_collect_report(
            self, report: protocol.CollectReportRepresentation) -> None:
        """Store the results of a test collection report."""
        #print(f'Collect report: {report.outcome}, {report.nodeid}', file=dlog)
        #if report.result:
        #    print('...results:', file=dlog)
        #    for obj in report.result:
        #        print(f'    {obj.__class__.__name__} {obj.name}', file=dlog)
        #if report.sections:
        #    print('...sections:', file=dlog)
        #    for obj in report.sections:
        #        print(f'    {obj}', file=dlog)
        if report.rich_traceback is not None:
            print(f'    {report.rich_traceback}', file=dlog)

        added, failed = self.test_state.add_collected_tests(report)
        if added:
            self.progress.update_bar(
                'collection',
                text=self.test_state.format_collection_progress())
        if failed:
            self.app.post_message(events.CollectionFailure(report.nodeid))

        if report.outcome != 'passed':
            print(
                f'Collect error: {report.nodeid!r}, {report.result}',
                file=collect_error_log)

    def proto_deselect_tests(self, items: list[pytest.Item]) -> None:
        """Note the deselection of one or more tests."""
        self.test_state.deselect_tests(items)

    def proto_test_collection_finish(self) -> None:
        """Handle the completion of test collection."""
        self.phase = ''
        self.stats.stop('Collection')
        self.progress.update_bar(
            'collection', text=self.test_state.format_collection_progress(),
            refresh=True)

    # Execution phase.
    def proto_start_run_phase(self) -> None:
        """Perform setup required when the run-phase starts."""
        self.phase = 'run'
        self.stats.start('Execution')
        progress = self.progress
        test_count_label = 'Parallel test count'
        test_count_name = 'running'

        if self.progress_mapper is None:
            # Create the progress bar mapper.
            region = self.app.main_region
            avail_size = region.width, region.height
            self.progress_mapper = TestProgressMapper(
                avail_size, self.app.root_path, self.test_state.items)

            # Add a progress bar for each grouping of tests.
            progress_mapper = self.progress_mapper
            for name, items in progress_mapper.item_map.items():
                label = progress_mapper.label_map.get(name, name)
                bar = SlottedProgressBar(label=label, nitems=len(items))
                progress.add_bar(name, bar=bar)

            # Add progress bars for the overall progress and current number of
            # active test runners, The latter only if pytest-xdist is active.
            bar = PercentageProgressBar(
                label='Overall', nitems=len(self.test_state.items))
            progress.add_bar('overall', bar=bar)
            nproc = self.numprocesses
            if nproc > 1:
                bar = GraphBar(label=test_count_label, max_count=nproc)
                progress.add_bar(test_count_name, bar=bar)
        else:
            # The number of processes may be changed from the last run.
            nproc = self.numprocesses
            if bar := progress.lookup_bar(test_count_name):
                proc_bar: GraphBar = cast(GraphBar, bar)
                if nproc == 1:
                    progress.remove_bar(test_count_name)
                else:
                    proc_bar.max_count = nproc
            elif nproc > 1:
                bar = GraphBar(label=test_count_label, max_count=nproc)
                progress.add_bar(test_count_name, bar=bar)

        progress.redraw()
        self._update_run_summary()

        for func in self.held_back_proto_calls:
            func()
        self.held_back_proto_calls[:] = []

    def proto_start_test(self, nodeid: str):
        """Handle the start of a test."""
        if self.phase != 'run':
            self.held_back_proto_calls.append(functools.partial(
                self.proto_start_test, nodeid))
        else:
            if result := self.test_state.start_test(nodeid):
                self.running_tests.add(nodeid)
                #print(f'Start: [{len(self.running_tests)}] {nodeid}', file=dlog)
                self._update_run_phase_progress(result)

    def proto_test_report(self, report: pytest.TestReport) -> None:
        """Log a report for the test run phase."""
        if self.phase != 'run':
            self.held_back_proto_calls.append(functools.partial(
                self.proto_test_report, report))
        else:
            if result := self.test_state.store_test_report(report):
                show = report.when == 'call'
                if 'test_focus.py' in result.nodeid or show:
                    worker = getattr(report, 'worker_id', None)
                    worker_id = f'[{worker}]' if worker is not None else ''
                    print(
                        f'Report{worker_id}: {report.when} {report.outcome}'
                        f' {report.nodeid}',
                        file=dlog)
                if result.finished and result.main_error_report:
                    self.failing_tests[result.nodeid] = result
                    self.app.post_message(events.TestFailure(result.nodeid))
                self._update_run_phase_progress(result)

    def proto_end_test(self, nodeid: str):
        """Handle the end of a test."""
        if self.phase != 'run':
            self.held_back_proto_calls.append(functools.partial(
                self.proto_end_test, nodeid))
        else:
            if result := self.test_state.end_test(nodeid):
                self.running_tests.discard(nodeid)
                #print(f'Stop: [{len(self.running_tests)}] {nodeid}', file=dlog)
                self._update_run_phase_progress(result)

    def _update_run_summary(self, *, include_timings: bool = False):
        """Update the summary part of the test progress area."""
        summary = self.test_state.format_summary(
            full=True, time_stats=self.stats if include_timings else None)
        w = self.app.query_one('#progress_summary')
        w.update(summary)

    def _update_run_phase_progress(self, result: NodeResult) -> None:
        # Update the progress bar for the specific test.
        self._update_test_progress(result)

        # Update the bars for the overall progress and active xdist workers.
        progress = self.progress
        n_finished, n_tests = self.test_state.completion_counts()
        progress.update_bar('overall', count=n_finished)
        progress.update_bar(
            'running', count=len(self.running_tests),
            refresh=n_finished >= n_tests)

    def _update_test_progress(self, result: NodeResult):
        """Update the test progress display for a single test."""
        nodeid = result.nodeid
        progress_mapper = self.progress_mapper
        if progress_mapper is None:
            return

        group_name = progress_mapper.group(nodeid)
        path_items = progress_mapper.items_for_test_group(nodeid)

        results = list(
            self.test_state.query_results([item.nodeid for item in path_items]))
        # TODO: Possibly should maintain finished counts per progress bar.
        n_finished = len([res for res in results if res.finished])
        idx = results.index(result)
        self.progress.update_bar(
            group_name, count=n_finished, idx=idx,
            result=result)

    # Miscellaneous pytest messages.
    def proto_warning_recorded(
            self,
            warning_message: warnings.WarningMessage,
            when: Literal['config', 'collect', 'runtest'],
            nodeid: str,
            filename: str | None,
            line_number: int | None,
            function: str | None,
        ) -> None:
        """Store and display a warning."""
        warning = PytestWarning(
            warning_message, when, nodeid, filename, line_number, function)
        self.app.dispatch_operation('show_warning', warning)

    #
    # Support for querying the model data.
    #
    def lookup_result(self, nodeid: str) -> NodeResult | None:
        """Find any `NodeResult` for a given pytest nodeid."""
        return self.test_state.items.get(nodeid)

    def lookup_collection_failure(self, nodeid: str) -> CollectReport | None:
        """Find any failure `CollectReport` for a given pytest nodeid."""
        return self.test_state.collect_failures.get(nodeid)


# TODO: Need to prevent attempts to add results to panels during shutdown.
class PytestApp(App):
    """A front-end to pytest to enhance execution and postmoretm analysis."""

    # pylint: disable=too-many-instance-attributes
    # pylint: disable=too-many-public-methods
    ENABLE_COMMAND_PALETTE = False
    DEFAULT_CSS = '''
        Screen TabbedContent {
            height: 1fr;
        }
    '''
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ('r', 'run', 'Run tests'),
        ('q', 'quit', 'Quit'),
        ('f1', 'show_tab("overview_pane")', 'Overview'),
        ('f2', 'show_tab("results_pane")', 'Postmortem'),
        ('f3', 'show_tab("config_pane")', 'Config'),
    ]
    if file_watching:
        BINDINGS.append(
            ('f4', 'show_tab("file_monitor_pane")', 'File monitoring'))
    BINDINGS.append(
        Binding('f9', 'show_tab("diag_pane")', 'Diagnostics', show=False))
    CSS_PATH = 'main.css'
    progress_update: int = reactive(0)

    def __init__(
            self,
            args: argparse.Namespace,
            config: configuration.Config,
        ):
        super().__init__()
        self.args = args
        self.config = config
        self.control = MainControl(app=self)
        self.dark = config.lookup_item('run-dark_mode').value
        self.checker: asyncio.Task | None = None
        self._run_config: ConfigRepresentation | None = None
        self.diag = DiagnoticsController(self)
        self.controllers = [
            self.control,
            OverviewController(self),
            CollectionController(self),
            PostMortemController(self),
            ConfigController(self),
        ]
        if file_watching:
            self.controllers.append(FileWatchController(self))
        self.controllers.append(self.diag)
        self.control.add_pytest_message_handler(self)
        for controller in self.controllers:
            if controller is not self.control:
                self.control.add_pytest_message_handler(controller)
        self.resizing_widget: Widget | None = None
        self.stdout = BufferedWriter(self, self.write_stdout)
        self.stderr = BufferedWriter(self, self.write_stderr)
        self.ticker: HIDTicker | None = None
        if self.args.hid_display:
            message_hook_context_var.set(self.message_hook)
        self._prev_message_time = 0.0
        self._track_watch_config_changes()
        self._idle_actions: list[Callable[[], None]] = []

    def compose(self) -> Iterator[Widget]:
        """Build up the widget tree."""
        yield Header()
        if self.args.hid_display:
            self.ticker = HIDTicker()
            yield self.ticker
        with TabbedContent(initial='overview_pane', id='main_screen'):
            for controller in self.controllers:
                #: if not getattr(controller, 'non_panel', False):
                yield from controller.compose()
        yield Footer()

    @property
    def root_path(self) -> Path:
        """The root directory (CWD when this program started)."""
        return ROOT

    @property
    def main_region(self) -> Path:
        """The Region instance of the main application area."""
        return self.app.query_one('#main_screen').region

    @property
    def run_config(self) -> pytest.Config:
        """The config for the most recent test run."""
        if self._run_config is None:
            msg = 'BUG: too soon for run_config'
            raise RuntimeError(msg)
        return self._run_config

    def _track_watch_config_changes(self):
        """Arrange to react to certain configuration changes."""
        self.config.lookup_item('run-dark_mode').register_for_changes(self)

    def handle_config_change(self, item: configuration.Item):
        """Handle change to a configuration item's value."""
        if item.cname == 'run-dark_mode':
            if item.value != self.dark:
                self.dark = item.value

    def message_hook(self, message: Message) -> None:
        """Show key and mouse events in the HID ticker."""
        if isinstance(message, (Key, events.HIDEvent)):
            if message.time > self._prev_message_time:
                self._prev_message_time = message.time
                if isinstance(message, events.HIDEvent):
                    self.ticker.add_event(message.description())
                else:
                    self.ticker.add_event(message.key)
                self.refresh()

    @on(Input.Changed)
    def handle_input_change(self, event: Input.Changed) -> None:
        """Process a change to an Input widget."""
        self.dispatch_operation('handle_input_change', event)

    @on(Checkbox.Changed)
    def handle_checkbox_change(self, event: Checkbox.Changed) -> None:
        """Process a change to a Checkbox widget."""
        self.dispatch_operation('handle_checkbox_change', event)

    @on(ListView.Selected)
    def handle_list_view_highlighted(
            self, event: ListView.Highlighted) -> None:
        """Process selection/highlight of list entry."""
        self.dispatch_operation('handle_list_view_highlighted', event)

    @on(Tree.NodeSelected)
    def handle_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Process selection fo a tree node."""
        self.dispatch_operation('handle_tree_node_selected', event)

    @on(events.TestSelectEvent)
    def distribute_test_selection(self, event: events.TestSelectEvent):
        """Distribute a test selection to all controllers."""
        self.dispatch_operation(
            'handle_test_run_selection', event.nodeid, selected=event.selected)

    @on(Mount)
    async def perform_mount_actions(self, _event: Mount):
        """Perform post-mount actions."""
        w = self.query_one('#main_screen')
        w.hide_tab('diag_pane')
        self.dispatch_operation('handle_mount')
        for controller in self.controllers:
            controller.start()
        await self.action_run()

    @on(events.ControlMessage)
    def forward_control_message(self, message: events.ControlMessage) -> None:
        """Simply forward (once) to all the controllers."""
        for controller in self.controllers:
            new_message = message.copy()
            new_message.stop()
            controller.post_message(new_message)

    @on(events.EndTestRunMessage)
    def handle_end_of_test_run(self) -> None:
        """Handle the end of a test run."""
        self.update_progress()
        self.update_reports()
        if self.config.lookup_item('run-jump_to_details').value:
            if self.control.collection_failures_occurred:
                self.action_show_tab('collection_pane')
            elif self.control.test_failures_occurred:
                self.action_show_tab('results_pane')
            else:
                self.action_show_tab('overview_pane')

    def action_show_tab(self, tab_id: str) -> None:
        """Switch to the TabPane with the given ID."""
        def switch_tab():
            print("DO SWITCH TAB", tab_id)
            self.set_focus(None)
            w.active = tab_id

        w = self.query_one('#main_screen')
        w.show_tab(tab_id)
        self._idle_actions.append(switch_tab)

    def update_progress(self):
        """Update the progress section of the display."""
        self.dispatch_operation('update_progress')

    def update_reports(self):
        """Update the progress section of the display."""
        self.dispatch_operation('update_reports')

    # TODO: Formalise the TEXTUAL_DEVTOOLS_PORT environment variable
    #       configuration.
    async def action_run(self):
        """Start exection of a test run."""
        os.environ['TEXTUAL_DEVTOOLS_PORT'] = '8081'
        await self.control.start_pytest()

    @on(Idle)
    def perform_idle_actions(self) -> None:
        """Perform and actions scheduled for idle time processing."""
        actions = list(self._idle_actions)
        self._idle_actions[:] = []
        for action in actions:
            action()

    @on(Button.Pressed)
    async def process_button(self, event: Button.Pressed) -> None:
        """Perform action for the pressed button."""
        m = {
            'run_all': AutoRunMode.ALL,
            'run_failing': AutoRunMode.ALL_FAILING,
            'run_selected_failing': AutoRunMode.SELECTED_FAILING,
        }
        self.control.start_pytest_for_selected_mode(
            m.get(event.button.id, AutoRunMode.ALL))

    def set_disabled(self):
        """Set the enabled/disabled state of various widgets."""
        for w in walk.walk_breadth_first(self):
            if w.has_class('not_when_running'):
                w.disabled = bool(self.control.pytest_is_running)

    def dispatch_operation(self, op_name: str, *args, **kwargs) -> list:
        """Dispatch an operation request to the controllers."""
        values = []
        for controller in self.controllers:
            method = getattr(controller, op_name, None)
            if method:
                values.append(method(*args, **kwargs))
        return [v for v in values if v is not None]

    def call_function(self, op_name: str, *args, **kwargs) -> object:
        """Call a function on the first responding controller.

        This is similar to `dispatch_operation`, but it stops once a value that
        is not ``None`` is returned. The value of the last method called is
        returned.
        """
        for controller in self.controllers:
            method = getattr(controller, op_name, None)
            if method and (ret := method(*args, **kwargs)) is not None:
                return ret
        return None

    #
    # Additional support for redirecting stdout and stderr to the TUI.
    #
    def copy_stdout(self, s: str):
        """Copy sys.stdout, via buffer, from a pytest run to the log view.

        This is invoked in response to a message from the background pytest
        process.
        """
        self.stdout.append(s)

    def copy_stderr(self, s: str):
        """Copy sys.stderr, via buffer, from a pytest run to the log view.

        This is invoked in response to a message from the background pytest
        process.
        """
        self.stderr.append(s)

    #
    # Handlers for messages from the pytest subprocess.
    #
    def proto_init(self, config: ConfigRepresentation) -> None:
        """Store information provided when a pytest run initialises.

        The `RichPipeReporter` pytest plugin sends this message when it
        initialises, providing the ConfigRepresentation for the test run.
        """
        self._run_config = config

    def write_stdout(self, s: str):
        """Copy sys.stdout from a pytest run to the log view.

        This is invoked in response to a message from the background pytest
        process.
        """
        w = self.query_one('#logging')
        w.add_message(f'Stdout: {s}')

    def write_stderr(self, s: str):
        """Copy sys.stdout from a pytest run to the log view.

        This is invoked in response to a message from the background pytest
        process.
        """
        w = self.query_one('#logging')
        w.add_message(f'Stderr: {s}')

    def write_line(self, line: str | bytes, **markup: bool) -> None:
        """Display an arbitrarty line of output.

        This is invoked in response to a message from the background pytest
        process.
        """
        print(f'Write line: {line!r}', file=dlog)
        w = self.query_one('#logging')
        w.add_message(line)

    def rich_write_line(self, line: str | bytes) -> None:
        """Display an arbitrarty line of output.

        This is invoked in response to a message from the background pytest
        process.
        """
        w = self.query_one('#logging')
        w.add_rich_message(line)

    #
    # Support for querying the model data.
    #
    def lookup_result(self, nodeid: str) -> NodeResult | None:
        """Find any `NodeResult` for a given pytest nodeid."""
        return self.control.lookup_result(nodeid)

    def lookup_collection_failure(self, nodeid: str) -> CollectReport | None:
        """Find any failure `CollectReport` for a given pytest nodeid."""
        return self.control.lookup_collection_failure(nodeid)


def is_in_top_line(r: Region, x, y):
    """Test whether a point lies in the top line of a region."""
    return r.x <= x < r.x + r.width and r.y == y


class HeightResizer:
    """A manager for adjusting a widget's height."""

    def __init__(self, w: Widget, y: int, max_inc: int):
        self.w = w
        self.base_y = y
        self.base_height = int(self.w.styles.height.value)
        self.prev_height = self.base_height
        self.height_range = range(4, self.base_height + max_inc + 1)
        self.dead = False

    def update(self, event: MouseEvent):
        """Change widget height in response to a mouse movement."""
        if self.dead:
            return

        delta = self.base_y - event.screen_y
        new_height = self.base_height + delta
        if new_height in self.height_range and new_height != self.prev_height:
            self.w.styles.height = new_height
            self.prev_height = new_height

    def max_height(self):
        """Calculate the maximum permitted height."""
