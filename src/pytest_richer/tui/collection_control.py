"""Controller for the test run collection failure panel."""
from __future__ import annotations

# TODO: Lot of duplication of the postmortem_control.py module.

from functools import partial
from typing import TYPE_CHECKING

from rich.console import Group
from textual import on
from textual.containers import (
    Horizontal, ScrollableContainer, Vertical, VerticalScroll)
from textual.widgets import (
    Button, Checkbox, Input, ListView, Static, TabPane, Tree)

import pytest_richer
from pytest_richer.tui import events
from pytest_richer.tui.control import Controller
from pytest_richer.tui.widgets import (
    ConfigCheckbox, Resizeable, SpinControl, TestListView, safe_int)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from textual.widget import Widget

    from . import app as tui_app, configuration
    from .model import NodeResult


PlainStatic = partial(Static, markup=False)

dlog = pytest_richer.get_log('collect-errors')


class ResizeableScrollableContainer(ScrollableContainer, Resizeable):
    """A ScrollableContainer with the ability to be resized by the user."""

    MIN_HEIGHT = 4
    MIN_TESTS_HEIGHT = 6

    def __init__(self, config: configuration.Config, **kwargs):
        super().__init__(**kwargs)
        self.init_resizer_part()
        self.config = config

    def calc_height(self, prev: int, new: int) -> int:
        """Calculate allowed height given a previous and desired height."""
        if new < self.MIN_HEIGHT:
            return self.MIN_HEIGHT
        elif new <= prev:
            return new
        else:
            # Prevent the collection_tree or test list from becoming too small.
            w = self.app.query_one('#collection_list_view')
            return prev + min(
                w.size.height - self.MIN_TESTS_HEIGHT, new - prev)


class CollectionPanel(TabPane):
    """Panel for the test collection failure view pane."""

    DEFAULT_CSS = '''
        CollectionPanel {
            layout: vertical;
        }
        CollectionPanel #collection_tree_view {
            height: 1fr;
        }
        CollectionPanel #collection_list_view {
            height: 1fr;
        }
        CollectionPanel #details_view {
            height: 4fr;
        }
        CollectionPanel Checkbox {
            height: 1;
            border: none;
            padding: 0 0 0 0;
        }
        CollectionPanel Button {
            border: none;
            margin: 0 0 0 1;
            height: 1;
        }
        CollectionPanel #collection_pane_show_stderr {
            margin: 0 0 0 2;
        }
        CollectionPanel #collection_pane_show_log {
            margin: 0 0 0 2;
        }
        CollectionPanel Button:focus {
            border: none;
        }
        CollectionPanel Button:hover {
            border: none;
        }
        CollectionPanel .input_label {
            width: 21;
        }
        CollectionPanel ConfigInput{
            border: none;
            width: 3;
        }
        CollectionPanel Checkbox:focus {
            border: none;
        }
        CollectionPanel #collection_pane_control {
            height: auto;
            dock: bottom;
        }
        CollectionPanel #collection_pane_view_control {
            height: auto;
        }
        CollectionPanel #collection_pane_actions {
            height: auto;
        }
        CollectionPanel .bordered {
            border: solid;
        }
    '''

    def __init__(self, control: CollectionController):
        super().__init__('Collection', id='collection_pane')
        self.control = control

    def compose(self) -> Iterator[Widget]:
        """Build up the widget tree for the result browser pane."""
        conf = self.control.config
        with VerticalScroll(id='collection_list_view', classes='bordered') as v:
            v.border_title = 'Collection failures'
            yield TestListView(id='collection_list')
        with ResizeableScrollableContainer(
                config=conf, id='details_view', classes='bordered') as v:
            v.border_title = 'Details'
            yield PlainStatic(id='collection_report_details')
        yield from self.compose_controls(conf)

    def compose_controls(self, conf: configuration.Config) -> Iterator[Widget]:
        """Build up the display controls widget tree."""
        def check_box(label: str, details_name: str, **kwargs):
            config = self.control.config
            item = config.lookup_item(f'collection-details-{details_name}')
            cb = ConfigCheckbox(
                label=label, item=item, classes='control_checkbox', **kwargs)
            return cb

        with Vertical(id='collection_pane_control', classes='bordered'):
            with Vertical(id='collection_pane_view_control') as v:
                v.border_title = 'View Control'

                with Horizontal():
                    yield check_box(
                        'Show locals:  ', 'locals',
                        id='collection_pane_show_locals')
                    item=conf.lookup_item(
                        'collection-details-max_container_length')
                    yield SpinControl(
                        item=item,
                        label='Max length (1-100):  ', value='5',
                        limits=range(1, 101), id='collection_pane_max_length')
                    item=conf.lookup_item(
                        'collection-details-max_string_length')
                    yield SpinControl(
                        item=item,
                        label='Max chars (4-256):', value='40',
                        id='collection_pane_max_string', limits=range(1, 257))

                with Horizontal():
                    yield check_box(
                        'Show context: ', 'context',
                        id='collection_pane_show_context')
                    item=conf.lookup_item('collection-details-context_length')
                    yield SpinControl(
                        item=item,
                        label='Context (0-30):      ', value='3',
                        id='collection_pane_context', limits=range(0, 31))

                with Horizontal():
                    yield check_box(
                        'Simple stack', 'simple_stack',
                        id='collection_pane_show_simple_stack')
                    yield check_box(
                        'Show stdout', 'stdout',
                        id='collection_pane_show_stdout')
                    yield check_box(
                        'Show stderr', 'stderr',
                        id='collection_pane_show_stderr')
                    yield check_box(
                        'Show log', 'logging', id='collection_pane_show_log')


class CollectionController(Controller):
    """Controller for the test collection failure view pane."""

    def __init__(self, app: tui_app.PytestApp):
        super().__init__(app)
        self.selected_test: str = ''
        self.panel: CollectionPanel

    def compose(self) -> Iterator[Widget]:
        """Build up the widget tree for the result browser pane."""
        self.panel = CollectionPanel(control=self)
        yield self.panel

    def handle_collection_run_selection(
            self, nodeid: str, *, selected: bool) -> None:
        """Handle the selection/deselection of a test to be (re)run."""
        w = self.panel.query_one(TestListView)
        w.set_collection_selection(nodeid, selected=selected)

    @property
    def context_lines(self) -> int:
        """How many context lines to show."""
        return safe_int(self.app.query_one('#collection_pane_context').value)

    @property
    def max_string(self) -> int:
        """Maximum length of string locals."""
        return safe_int(self.app.query_one('#collection_pane_max_string').value)

    @property
    def max_length(self) -> int:
        """How collection entryies to show."""
        return safe_int(self.app.query_one('#collection_pane_max_length').value)

    @property
    def show_locals(self) -> bool:
        """Whether or not to show locals."""
        return self.app.query_one('#collection_pane_show_locals').value

    @property
    def show_context(self) -> bool:
        """Whether or not to show context lines."""
        return self.app.query_one('#collection_pane_show_context').value

    @property
    def show_simple_stack(self) -> bool:
        """Whether or not to show a simplified stack."""
        return self.app.query_one('#collection_pane_show_simple_stack').value

    @property
    def show_stdout(self) -> bool:
        """Whether or not to show captured stdout lines."""
        return self.app.query_one('#collection_pane_show_stdout').value

    @property
    def show_stderr(self) -> bool:
        """Whether or not to show captured stderr lines."""
        return self.app.query_one('#collection_pane_show_stderr').value

    @property
    def show_log(self) -> bool:
        """Whether or not to show captured log lines."""
        return self.app.query_one('#collection_pane_show_log').value

    def reset(self):
        """Reset the ready for a new test run."""
        self.app.query_one('#collection_list').clear()
        w = self.app.query_one('#collection_report_details')
        w.update('')

    @on(events.CollectionFailure)
    def process_collection_failure(
            self, event: events.CollectionFailure) -> None:
        """Update result information for collection failure."""
        collection_id = event.nodeid
        w = self.app.query_one('#collection_list')
        w.add_collection_failure(collection_id)
        if len(w) == 1:
            self.selected_test = collection_id
        self.handle_collection_selection()

    def _result_for_nodeid(self, nodeid: str) -> NodeResult | None:
        if reporter := self.app.reporter:
            if run_phase:= reporter.run_phase:
                result, _ = run_phase.result_for_nodeid(nodeid)
                return result
        return None

    def handle_collection_selection(self) -> None:
        """React to user selecting an item from the test list."""
        w = self.app.query_one('#collection_report_details')
        result = self.app.lookup_collection_failure(self.selected_test)
        content: Group | str = 'Nothing to report'
        print(f'Collect fail: {self.selected_test} {result=}', file=dlog)
        if result:
            n_context = self.context_lines if self.show_context else 0
            if content := result.format_failure(
                    n_context=n_context, show_locals=self.show_locals,
                    max_string=self.max_string, max_length=self.max_length,
                    dark_bg=self.app.dark,
                    show_simple_stack=self.show_simple_stack):
                sections = []
                if self.show_stdout:
                    sections.append(result.format_section('stdout'))
                if self.show_stderr:
                    sections.append(result.format_section('stderr'))
                if self.show_log:
                    sections.append(result.format_section('log'))
                sections = [s for s in sections if s is not None]
                content = Group(content, *sections)
            else:
                content = 'Nothing to report'
        w.update(content)

    def handle_input_change(self, event: Input.Changed) -> None:
        """Process a change to an Input widget."""
        self.handle_collection_selection()

    def handle_checkbox_change(self, event: Checkbox.Changed) -> None:
        """Process a change to a Checkbox widget."""
        self.handle_collection_selection()

    def handle_list_view_highlighted(
            self, event: ListView.Highlighted) -> None:
        """Process selection/highlight of list entry."""
        if event.list_view.id == 'collection_list' and event.item:
            nodeid = event.item.name
            self.selected_test = nodeid
            self.handle_collection_selection()
