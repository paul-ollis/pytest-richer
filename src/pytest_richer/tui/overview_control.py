"""Controller for the overview panel."""
from __future__ import annotations

from typing import TYPE_CHECKING
from functools import partial

from textual import on
from textual.containers import (
    Horizontal, ScrollableContainer, Vertical, VerticalScroll)
from textual.widgets import Button, Static, TabPane

from .widgets import (
    CompactButton, ConfigCheckbox, ConfigCompactSelect,
    ConfigurationItemVisibilityControlled, FlatButton, LimitedMessageView,
    Resizeable, ShortButton, TestListView)
from pytest_richer.tui import events
from pytest_richer.tui.configuration import AutoRunMode
from pytest_richer.tui.control import Controller
from pytest_richer.tui.test_progress import ProgressDisplay

if TYPE_CHECKING:
    from collections.abc import Iterator

    from textual.widget import Widget

    from . import app as tui_app, configuration, test_types

PlainStatic = partial(Static, markup=False)


class ResizeableScrollableContainer(ScrollableContainer, Resizeable):
    """A ScrollableContainer with the ability to be resized by the user."""

    def __init__(self, **kwargs):
        classes = f'{kwargs.pop("classes", "")} bordered'
        super().__init__(classes=classes, **kwargs)
        self.init_resizer_part()

    def calc_height(self, prev: int, new: int) -> int:
        """Calculate allowed height given a previous and desired height."""
        if new < 4:                                             # noqa: PLR2004
            return 4
        elif new <= prev:
            return new
        else:
            # Prevent the progress bar display from dropping below 6 lines.
            w = self.app.query_one('#progress_view')
            return prev + min(w.size.height - 6, new - prev)


class HideableResizeableScrollableContainer(
        ConfigurationItemVisibilityControlled, ResizeableScrollableContainer):
    """A hideable version of `ResizeableScrollableContainer`."""


class OverviewPanel(TabPane):
    """Panel for the overview pane."""

    DEFAULT_CSS = '''
        OverviewPanel {
            layout: vertical;
        }
        OverviewPanel Checkbox {
            height: 1;
            border: none;
            padding: 0 0 0 0;
            margin: 0 2 0 0;
        }
        OverviewPanel Checkbox:focus {
            border: none;
        }
        OverviewPanel #bottom_dock {
            height: auto;
            dock: bottom;
        }
        OverviewPanel #overview_pane_view_control {
            height: auto;
            border: solid $primary-lighten-2;
        }
        OverviewPanel Button {
            margin: 0 0 0 1;
        }
        /*
        OverviewPanel Button {
            border: none;
            margin: 0 0 0 1;
            height: 1;
        }
        OverviewPanel Button:focus {
            border: none;
        }
        OverviewPanel Button:hover {
            border: none;
        }
        */
        OverviewPanel #overview_pane_actions {
            height: auto;
        }
        OverviewPanel .bordered {
            border: solid;
        }
        OverviewPanel #auto-run-label {
            width: auto;
        }
    '''

    def __init__(self, control: OverviewController):
        super().__init__('Overview', id='overview_pane')
        self.control = control

    def compose(self) -> Iterator[Widget]:
        """Build up the widget tree for the overview pane."""
        conf = self.control.config
        with ScrollableContainer(id='progress_view', classes='bordered') as v:
            v.border_title = 'Progress'
            yield ProgressDisplay(id='progress_display')
            yield PlainStatic(id='progress_summary', classes='bordered')
        with ResizeableScrollableContainer(id='collect_failed_view') as v:
            v.border_title = 'Collection Issues'
            v.styles.height = 6
            yield TestListView(id='collect_failed_list')
        with HideableResizeableScrollableContainer(
                item=conf.lookup_item('overview-show-failures'),
                id='failed_view',
            ) as v:
            v.border_title = 'Failures'
            v.styles.height = 6
            yield TestListView(id='failed_list')
        with HideableResizeableScrollableContainer(
                item=conf.lookup_item('overview-show-warnings'),
                id='warning_view',
            ) as v:
            v.border_title = 'Warnings'
            v.styles.height = 6
            yield LimitedMessageView(id='warnings', max_entries=0)
        with HideableResizeableScrollableContainer(
                item=conf.lookup_item('overview-show-logging'),
                id='logging_view',
            ) as v:
            v.border_title = 'Logging'
            v.styles.height = 6
            yield LimitedMessageView(id='logging', max_entries=500)

        with Vertical(id='bottom_dock'):
            with Vertical(id='overview_pane_view_control') as v:
                v.border_title = 'View Control'
                with Horizontal():
                    yield ConfigCheckbox(
                        item=conf.lookup_item('overview-show-failures'),
                        label='Failures')
                    yield ConfigCheckbox(
                        item=conf.lookup_item('overview-show-warnings'),
                        label='Warnings')
                    yield ConfigCheckbox(
                        item=conf.lookup_item('overview-show-logging'),
                        label='Logging')

            with Vertical(id='overview_pane_actions', classes='bordered') as a:
                a.border_title = 'Run'
                with Horizontal(id='run_actions'):
                    yield ShortButton(
                        label='All', id='run_all', classes='not_when_running')
                    yield ShortButton(
                        label='Failing', id='run_failing',
                        classes='not_when_running')
                    yield ShortButton(
                        label='Selected failing', id='run_selected_failing',
                        classes='not_when_running')

                    yield PlainStatic('Auto-run', id='auto-run-label')
                    options = [
                        ('All failing', AutoRunMode.ALL_FAILING),
                        ('Select failing', AutoRunMode.SELECTED_FAILING),
                        ('All', AutoRunMode.ALL),
                    ]
                    yield ConfigCompactSelect(
                        options=options, id='auto_run_mode',
                        item=conf.lookup_item('run-auto_run_mode'),
                        allow_blank=False)


class OverviewController(Controller):
    """Controller for the overview view pane."""

    def __init__(self, app: tui_app.PytestApp):
        super().__init__(app)
        self.panel: OverviewPanel

    def compose(self) -> Iterator[Widget]:
        """Build up the widget tree for the overiview pane."""
        self.panel = OverviewPanel(control=self)
        yield self.panel

    def handle_test_run_selection(
            self, nodeid: str, *, selected: bool) -> None:
        """Handle the selection/deselection of a test to be (re)run."""
        w = self.panel.query_one(TestListView)
        w.set_test_selection(nodeid, selected=selected)

    def selected_failing_nodeid_set(self) -> set[str]:
        """Return a pytest nodeid set of failing tests."""
        w = self.panel.query_one(TestListView)
        return w.selected_nodeid_set()

    def reset(self):
        """Reset the view for a new test run."""
        self.app.query_one('#failed_list').hide_all()
        self.app.query_one('#warnings').clear()
        self.app.query_one('#collect_failed_list').clear()

    def show_warning(self, warning: test_types.PytestWarning):
        """Add a warning to the display."""
        w = self.app.query_one('#warnings')
        w.add_rich_renderable(warning.to_text())

    @on(events.CollectionFailure)
    def process_collection_failure(
            self, event: events.CollectionFailure) -> None:
        """Update collection failure information for a node."""
        w = self.app.query_one('#collect_failed_list')
        w.add_collection_failure(event.nodeid)

    @on(events.TestFailure)
    def process_result(self, event: events.TestFailure) -> None:
        """Update result information for a node."""
        w = self.app.query_one('#failed_list')
        w.add_test(event.nodeid)
