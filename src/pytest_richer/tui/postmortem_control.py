"""Controller for the test run postmortem panel."""
from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from rich.console import Group
from textual import on
from textual.containers import (
    Horizontal, ScrollableContainer, Vertical, VerticalScroll)
from textual.widgets import (
    Button, Checkbox, Input, ListView, Static, TabPane, Tree)

from pytest_richer.tui import events
from pytest_richer.tui.control import Controller
from pytest_richer.tui.widgets import (
    ConfigCheckbox, ConfigurationItemVisibilityControlled, ConfigCheckbox,
    Resizeable, SpinControl, TestListView, TestTree, safe_int)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from textual.widget import Widget

    from pytest_richer.tui import app as tui_app, configuration
    from pytest_richer.tui.model import NodeResult

PlainStatic = partial(Static, markup=False)


# TODO: Factor out common base class. See collection_control.py and
#       overview_control.py.
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
            # Prevent the test_tree or test list from becoming too small.
            show_tree = self.config.lookup_item('postmortem-show-tree')
            if show_tree.value:
                w = self.app.query_one('#test_tree_view')
            else:
                w = self.app.query_one('#test_list_view')
            return prev + min(
                w.size.height - self.MIN_TESTS_HEIGHT, new - prev)


class HideableVerticalScroll(
        ConfigurationItemVisibilityControlled, VerticalScroll):
    """VerticalScroll that can be hidden by configuration."""


class PostmortemPanel(TabPane):
    """Panel for the test post mortem view pane."""

    DEFAULT_CSS = '''
        PostmortemPanel {
            layout: vertical;
        }
        PostmortemPanel #test_tree_view {
            height: 1fr;
        }
        PostmortemPanel #test_list_view {
            height: 1fr;
        }
        PostmortemPanel #details_view {
            height: 4fr;
        }
        PostmortemPanel TestTree {
            width: 1fr;
            max-width: 1fr;
            height: auto;
        }
        PostmortemPanel Checkbox {
            height: 1;
            border: none;
            padding: 0 0 0 0;
        }
        PostmortemPanel Button {
            border: none;
            margin: 0 0 0 1;
            height: 1;
        }
        PostmortemPanel #results_pane_show_stderr {
            margin: 0 0 0 2;
        }
        PostmortemPanel #results_pane_show_log {
            margin: 0 0 0 2;
        }
        PostmortemPanel Button:focus {
            border: none;
        }
        PostmortemPanel Button:hover {
            border: none;
        }
        PostmortemPanel .input_label {
            width: 21;
        }
        PostmortemPanel ConfigInput{
            border: none;
            width: 3;
        }
        PostmortemPanel Checkbox:focus {
            border: none;
        }
        PostmortemPanel #results_pane_control {
            height: auto;
            dock: bottom;
        }
        PostmortemPanel #results_pane_view_control {
            height: auto;
        }
        PostmortemPanel #results_pane_actions {
            height: auto;
        }
        PostmortemPanel .bordered {
            border: solid;
        }
    '''

    def __init__(self, control: PostMortemController):
        super().__init__('Postmortem', id='results_pane')
        self.control = control

    def compose(self) -> Iterator[Widget]:
        """Build up the widget tree for the result browser pane."""
        conf = self.control.config
        show_tree = conf.lookup_item('postmortem-show-tree')
        with HideableVerticalScroll(
                item=show_tree, id='test_tree_view', classes='bordered'):
            yield TestTree(label='Tests', id='test_tree')
        with HideableVerticalScroll(
                item=show_tree, invert_control=True, id='test_list_view',
                classes='bordered') as v:
            v.border_title = 'Failures'
            yield TestListView(id='test_list')
        with ResizeableScrollableContainer(
                config=conf, id='details_view', classes='bordered') as v:
            v.border_title = 'Details'
            yield PlainStatic(id='test_report_details')
        yield from self.compose_controls(conf)

    def compose_controls(self, conf: configuration.Config) -> Iterator[Widget]:
        """Build up the display controls widget tree."""
        def check_box(label: str, details_name: str, **kwargs):
            config = self.control.config
            item = config.lookup_item(f'postmortem-details-{details_name}')
            cb = ConfigCheckbox(
                label=label, item=item, classes='control_checkbox', **kwargs)
            return cb

        with Vertical(id='results_pane_control', classes='bordered'):
            with Vertical(id='results_pane_view_control') as v:
                v.border_title = 'View Control'

                with Horizontal():
                    yield ConfigCheckbox(
                        item=conf.lookup_item('postmortem-show-tree'),
                        label='Tree view')

                with Horizontal():
                    yield check_box(
                        'Show locals:  ', 'locals',
                        id='results_pane_show_locals')
                    item=conf.lookup_item(
                        'postmortem-details-max_container_length')
                    yield SpinControl(
                        item=item,
                        label='Max length (1-100):  ', value='5',
                        limits=range(1, 101), id='results_pane_max_length')
                    item=conf.lookup_item(
                        'postmortem-details-max_string_length')
                    yield SpinControl(
                        item=item,
                        label='Max chars (4-256):', value='40',
                        id='results_pane_max_string', limits=range(1, 257))

                with Horizontal():
                    yield check_box(
                        'Show context: ', 'context',
                        id='results_pane_show_context')
                    item=conf.lookup_item('postmortem-details-context_length')
                    yield SpinControl(
                        item=item,
                        label='Context (0-30):      ', value='3',
                        id='results_pane_context', limits=range(0, 31))

                with Horizontal():
                    yield check_box(
                        'Simple stack', 'simple_stack',
                        id='results_pane_show_simple_stack')
                    yield check_box(
                        'Show stdout', 'stdout',
                        id='results_pane_show_stdout')
                    yield check_box(
                        'Show stderr', 'stderr',
                        id='results_pane_show_stderr')
                    yield check_box(
                        'Show log', 'logging',
                        id='results_pane_show_log')

            with Vertical(id='results_pane_actions', classes='bordered') as v:
                v.border_title = 'Run'
                with Horizontal(id='run_actions'):
                    yield Button(
                        'All', id='run_all', classes='not_when_running')
                    yield Button(
                        'Failing', id='run_failing',
                        classes='not_when_running')
                    yield Button(
                        'Selected failing', id='run_selected_failing',
                        classes='not_when_running')


class PostMortemController(Controller):
    """Controller for the test post mortem view pane."""

    def __init__(self, app: tui_app.PytestApp):
        super().__init__(app)
        self.selected_test: str = ''
        self.panel: PostmortemPanel

    def compose(self) -> Iterator[Widget]:
        """Build up the widget tree for the result browser pane."""
        self.panel = PostmortemPanel(control=self)
        yield self.panel

    def handle_test_run_selection(
            self, nodeid: str, *, selected: bool) -> None:
        """Handle the selection/deselection of a test to be (re)run."""
        w = self.panel.query_one(TestTree)
        w.set_test_selection(nodeid, selected=selected)
        w = self.panel.query_one(TestListView)
        w.set_test_selection(nodeid, selected=selected)

    @property
    def context_lines(self) -> int:
        """How many context lines to show."""
        return safe_int(self.app.query_one('#results_pane_context').value)

    @property
    def max_string(self) -> int:
        """Maximum length of string locals."""
        return safe_int(self.app.query_one('#results_pane_max_string').value)

    @property
    def max_length(self) -> int:
        """How collection entryies to show."""
        return safe_int(self.app.query_one('#results_pane_max_length').value)

    @property
    def show_locals(self) -> bool:
        """Whether or not to show locals."""
        return self.app.query_one('#results_pane_show_locals').value

    @property
    def show_context(self) -> bool:
        """Whether or not to show context lines."""
        return self.app.query_one('#results_pane_show_context').value

    @property
    def show_simple_stack(self) -> bool:
        """Whether or not to show a simplified stack."""
        return self.app.query_one('#results_pane_show_simple_stack').value

    @property
    def show_stdout(self) -> bool:
        """Whether or not to show captured stdout lines."""
        return self.app.query_one('#results_pane_show_stdout').value

    @property
    def show_stderr(self) -> bool:
        """Whether or not to show captured stderr lines."""
        return self.app.query_one('#results_pane_show_stderr').value

    @property
    def show_log(self) -> bool:
        """Whether or not to show captured log lines."""
        return self.app.query_one('#results_pane_show_log').value

    def reset(self):
        """Reset the ready for a new test run."""
        self.app.query_one('#test_list').clear()
        self.app.query_one('#test_tree').clear()
        w = self.app.query_one('#test_report_details')
        w.update('')

    @on(events.TestFailure)
    def process_test_failure(self, event: events.TestFailure) -> None:
        """Update result information for test failure."""
        test_id = event.nodeid
        w = self.app.query_one('#test_list')
        w.add_test(test_id)
        if len(w) == 1:
            self.selected_test = test_id

        w = self.app.query_one('#test_tree')
        w.add_test(test_id)
        self.handle_test_selection()

    def _result_for_nodeid(self, nodeid: str) -> NodeResult | None:
        if reporter := self.app.reporter:
            if run_phase:= reporter.run_phase:
                result, _ = run_phase.result_for_nodeid(nodeid)
                return result
        return None

    def handle_test_selection(self) -> None:
        """React to user selecting an item from the test list."""
        w = self.app.query_one('#test_report_details')
        result = self.app.lookup_result(self.selected_test)
        content: Group | str = 'Nothing to report'
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
        self.handle_test_selection()

    def handle_checkbox_change(self, event: Checkbox.Changed) -> None:
        """Process a change to a Checkbox widget."""
        self.handle_test_selection()

    def handle_list_view_highlighted(
            self, event: ListView.Highlighted) -> None:
        """Process selection/highlight of list entry."""
        if event.list_view.id == 'test_list' and event.item:
            nodeid = event.item.name
            w = self.app.query_one('#test_tree')
            node = w.get_node_by_pytest_nodeid(nodeid)
            w.select_node(node)
            self.selected_test = nodeid
            self.handle_test_selection()

    def handle_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Process selection fo a tree node."""
        if event.control.id == 'test_tree':
            if nodeid := event.node.data.nodeid:
                w = self.app.query_one('#test_list')
                for i, item in enumerate(w.children):
                    if item.name == nodeid:
                        w.index = i
                        break
                self.selected_test = nodeid
                self.handle_test_selection()
