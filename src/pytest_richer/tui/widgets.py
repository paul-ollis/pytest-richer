"""TUI widgets."""
from __future__ import annotations

from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from functools import partial
from typing import Any, Callable, TYPE_CHECKING
from weakref import proxy

from rich.style import Style
from rich.text import Text
from textual import on
from textual.containers import Horizontal, VerticalScroll
from textual.events import (
    Click, Key, Mount, MouseDown, MouseEvent, MouseMove, MouseUp)
from textual.message import Message
from textual.validation import Number
from textual.widget import Widget
from textual.widgets import (
    Button, Checkbox, Input, Label, ListItem, ListView, Select, Static, Tree)
from textual.widgets._tree import TOGGLE_STYLE
from textual.widgets.tree import UnknownNodeID

import pytest_richer
from pytest_richer.tui.configuration import ConfigValueType
from pytest_richer.tui.events import HIDEvent, TestSelectEvent
from pytest_richer.tui.test_types import TestID

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Callable, Literal

    from rich.console import RenderableType
    from textual.geometry import Region
    from textual.widgets.tree import TreeNode

    from pytest_richer.tui.configuration import Item

dlog = pytest_richer.get_log('main')

LEFT = 1
RIGHT = 3

PlainStatic = partial(Static, markup=False)


def safe_int(s: str, default: int = 0):
    """Convert a string to an integer or a default value upon failure."""
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


class SelectionState(Enum):
    """The possible selection states for a node in a Testtree."""

    UNSELECTED = 1
    PARTIALLY_SELECTED = 2
    SELECTED = 3


class ConfigurationItemReflector:
    """A mix-in for a widget that reflects a configuration value."""

    def __init__(self, item: configuration.Item, **kwargs):
        super().__init__(**kwargs)
        self._item = item
        value = self.value
        if isinstance(value, str):
            self.value = str(self._item.value)
        else:
            self.value = self._item.value
        self.watch(self, 'value', self._push_to_config)

    def _push_to_config(self, new_value: str) -> None:
        value = self._item.value
        if isinstance(value, str):
            self._item.value = str(new_value)
        else:
            self._item.value = new_value


class ConfigurationItemVisibilityControlled:
    """A mix-in linking a widget's display to a configutation item."""

    def __init__(
            self, item: configuration.Item, invert_control: bool = False,
            **kwargs):
        super().__init__(**kwargs)
        self._invert_control = invert_control
        self.display = True
        self.handle_config_change(item)
        item.register_for_changes(self)

    def handle_config_change(self, item: Item) -> None:
        """Respond to a change of an item's value."""
        if self._invert_control:
            self.display = not item.value
        else:
            self.display = item.value


@dataclass
class TreeNodeData:
    """Data stored on a tree node.

    @level_id:
        A string identifying this node, made up of one component per level
        within the tree. All progeny nodes' level_id start with this string.
    @select_state:
        A `SelectionState` enumeration. For leaf nodes this is always
        `UNSELECTED` or `SELECTED`. Other nodes may also have the value
        `PARTIALLY_SELECTED`.
    @nodeid:
        The pytest nodeid. This is an empty string for non-leaf nodes.
    """

    level_id: str
    select_state: SelectionState = SelectionState.SELECTED
    nodeid: str = ''


class HIDTicker(Widget):
    """A scrolling ticker display showing mouse and keyboard input."""

    MAX = 20

    DEFAULT_CSS = '''
        HIDTicker {
            height: 1;
            background: $primary-darken-3;
        }
        HIDTicker Label.prefix {
            color: $text-muted;
        }
        HIDTicker Label.separator {
            margin: 0 1 0 1;
        }
        HIDTicker Static.current {
            width: auto;
            min_width: 5;
        }
        HIDTicker Static.history {
            width: 1fr;
            color: $text-disabled;
        }
    '''

    def __init__(self):
        super().__init__()
        self.recent_events: deque[str] = deque((), maxlen=self.MAX)
        self.current: Static | None = None
        self.history: Static | None = None
        self.last_ev: str = ''
        self.repeat = 0

    def compose(self) -> Iterator[Widget]:
        """Build the widget hierarchy."""
        self.current = PlainStatic(classes='current')
        self.history = PlainStatic(classes='history')
        with Horizontal():
            yield Label('Actions: ', classes='prefix')
            yield self.current
            yield Label('║', classes='separator')
            yield self.history

    def add_event(self, ev: str):
        """Add an event to the ticker display."""
        def fmt_event():
            if self.repeat:
                return f'{self.repeat+1}☓{self.last_ev}'
            else:
                return f'{self.last_ev}'

        if ev == self.last_ev:
            self.repeat += 1
        else:
            self.recent_events.appendleft(fmt_event())
            self.last_ev = ev
            self.repeat = 0
        if self.current:
            self.current.update(fmt_event())
        if self.history:
            self.history.update(' .. '.join(self.recent_events))


class LimitedMessageView(VerticalScroll):
    """Widget to show a limited sequence of message.

    - Each message is rendered in its own widget.
    - The view scrolls to show new messages as they are added.
    - Older entries are removed once `max_entries` is reached.

    :max_entries:
        The maximum number of entries to retain. If this is less than 1 no
        limit is imposed.
    """

    DEFAULT_CSS = '''
        LimitedMessageView {
            layout: vertical;
        }
        '''

    def __init__(self, max_entries: int = 30, **kwargs):
        super().__init__(**kwargs)
        self.max_entries = max_entries

    def compose(self) -> Iterator[Widget]:
        """Build the widget hierarchy."""
        yield from []

    def add_message(self, message: str) -> None:
        """Add a simple text message."""
        self._add_entry(Static(Text(message)))

    def add_rich_message(self, message: str) -> None:
        """Add a text message that uses Rich markup."""
        self._add_entry(Static(message))

    def add_rich_renderable(self, message: RenderableType) -> None:
        """Add a Rich RenderableType as a message."""
        self._add_entry(PlainStatic(message))

    def clear(self) -> None:
        """Clear all messages."""
        self.remove_children()

    def _add_entry(self, w: Widget) -> None:
        self.mount(w)
        if self.max_entries >= 1:
            to_remove = len(self.children) - self.max_entries
            if to_remove > 0:
                for i in range(to_remove):
                    self.children[i].remove()
        w.scroll_visible()


class SelectableTestItem(ListItem):
    """A TestItem holding a checkbox and pytest nodeid."""

    DEFAULT_CSS = '''
        SelectableTestItem {
            layout: horizontal;
        }
        '''

    def __init__(self, nodeid: str, **kwargs):
        super().__init__(name=nodeid, **kwargs)

    def compose(self) -> Iterator[Widget]:
        """Build the widget hierarchy."""
        yield Checkbox(value=True, label='', name=self.name)
        yield PlainStatic(self.name)


class TestListView(ListView):
    """A list view for tests or collection failures.

    Each test entry has a selection check box and the test's nodeid. Collection
    failure entries only have the nodeid.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._test_set: dict[str, SelectableTestItem] = {}

    def add_collection_failure(self, nodeid: str) -> None:
        """Add collection failure nodeid to the list."""
        if nodeid in self._test_set:
            self._test_set[nodeid].display = True
        else:
            item = ListItem(PlainStatic(nodeid))
            self.append(item)
            self._test_set[nodeid] = item

    def add_test(self, nodeid: str) -> None:
        """Add a test to the list."""
        if nodeid in self._test_set:
            self._test_set[nodeid].display = True
        else:
            item = SelectableTestItem(nodeid=nodeid)
            self.append(item)
            self._test_set[nodeid] = item

    @on(Key)
    def perform_special_key_handling(self, event: Key):
        """Perform special key processing."""
        # pylint: disable=no-self-use
        print(f'Key: {event.key}')

    def get_checkbox_for_nodeid(self, nodeid: str) -> Checkbox | None:
        """Find the checkbox associated with a given test."""
        items = self.query(SelectableTestItem)
        for item in items.nodes:
            if item.name == nodeid:
                return item.query_one(Checkbox)
        return None

    def set_test_selection(self, nodeid: str, *, selected: bool) -> None:
        """Set the selected state of a test entry.

        This has no effect if the entry's state does not need to change.

        :nodeid:   The pytest nodeid.
        :selected: True if the test should be selected.
        """
        cb = self.get_checkbox_for_nodeid(nodeid)
        if cb and cb.value != selected:
            with self.prevent(Checkbox.Changed):
                cb.value = selected

    def nodeid_set(self) -> set[str]:
        """Create a set of all the pytest nodeids in this list."""
        checkboxes = self.query(Checkbox)
        return {cb.name for cb in checkboxes.nodes}

    def clear(self):
        """Clear all items from the list."""
        super().clear()
        self._test_set.clear()

    def hide_all(self):
        """Hide all the items in this list."""
        for child in self.children:
            child.display = False

    def selected_nodeid_set(self) -> set[str]:
        """Create a set of all the selected pytest nodeids in this list."""
        checkboxes = self.query(Checkbox)
        return {cb.name for cb in checkboxes.nodes if cb.value}

    @on(Checkbox.Changed)
    def handle_text_selection_change(self, event: Checkbox.Changed) -> None:
        """Process a change to a test selection."""
        cb = event.checkbox
        self.post_message(TestSelectEvent(cb.name, selected=cb.value))


class TestTree(Tree[TreeNodeData]):
    """A tree view for tests."""

    def __init__(self, *args, **kwargs):
        data = TreeNodeData(
            level_id='root', select_state=SelectionState.SELECTED)
        super().__init__(*args, data=data, **kwargs)
        self.nodeid_to_tree_id: dict[str, int] = {}
        self.level_id_to_tree_id: dict[str, int] = {}
        self.undo_select: int | None = None
        self.show_root = False
        self.root.expand()
        self.dirty_nodes: set[TreeNode] = set()
        self.selected: str = ''
        self._test_set: dict[str, TreeNode] = {}

    def add_test(self, test_id: TestID) -> None:
        """Add a test to the tree."""
        node = self.root
        trail = []
        for name in test_id.parts[:-1]:
            trail.append(name)
            level_id = f'ti_{"_".join(trail)}'
            existing_node = self.get_node_by_level_id(level_id)
            if existing_node is None:
                node = self.add_node(
                    node, name, expand=True, level_id=level_id)
            else:
                node = existing_node
        name = test_id.parts[-1]
        level_id = f'{level_id}_{name}'
        if test_id not in self._test_set:
            node = self.add_test_node(
                node, label=name, level_id=level_id, nodeid=test_id)
            self._test_set[test_id] = node
            print(f'TestTree: Add test {test_id}', file=dlog)
        else:
            print(f'TestTree: Existing test {test_id}', file=dlog)

    def add_node(self, node, label, level_id: str, *, expand: bool):
        """Add a node to an existing node."""
        data = TreeNodeData(level_id=level_id)
        new_node = node.add(label, data=data, expand=expand)
        self.level_id_to_tree_id[level_id] = new_node.id
        return new_node

    def add_test_node(self, node, label, *, level_id: str, nodeid: str):
        """Add a test (leaf) node to an existing node."""
        data = TreeNodeData(level_id=level_id, nodeid=nodeid)
        new_node = node.add_leaf(label.replace('[', '\\['), data=data)
        self.nodeid_to_tree_id[nodeid] = new_node.id
        self.level_id_to_tree_id[level_id] = new_node.id
        return new_node

    def get_node_by_pytest_nodeid(self, nodeid: str) -> TreeNode | None:
        """Find a node using a pytest nodeid."""
        with suppress(UnknownNodeID):
            return self.get_node_by_id(self.nodeid_to_tree_id.get(nodeid, -1))
        return None

    def get_node_by_level_id(self, level_id: str) -> TreeNode | None:
        """Find a node using a pytest nodeid."""
        with suppress(UnknownNodeID):
            return self.get_node_by_id(
                self.level_id_to_tree_id.get(level_id, -1))
        return None

    def tree_node_from_event(self, event: Click) -> TreeNode | None:
        """Get the tree node for an event."""
        meta = None
        with suppress(AttributeError):
            meta = event.style.meta
        if meta and 'line' in meta:
            return self.get_node_at_line(meta['line'])
        else:
            return None

    def clear(self):
        """Clear all nodes under root."""
        super().clear()
        self.nodeid_to_tree_id = {}
        self.level_id_to_tree_id = {}
        self.dirty_nodes = set()
        self._test_set.clear()

    def _toggle_tree_selection(self, node: TreeNode):
        self.undo_select = self.cursor_line
        data = node.data
        if data.select_state == SelectionState.SELECTED:
            new_state = SelectionState.UNSELECTED
        else:
            new_state = SelectionState.SELECTED
        self._set_recursive_selection(node, state=new_state)
        self._update_ancestry_completeness(node)
        for sub_node in self.dirty_nodes:
            if hasattr(sub_node, 'refresh'):
                sub_node.refresh()
            else:
                # A temporary work-around while Textual-0.4.2 is the latest
                # release.
                sub_node.label = sub_node.label
                self.refresh_line(sub_node.line)
        self.dirty_nodes.clear()

    async def on_click(self, event: Click):
        """Handle user click on on this tree."""
        if node := self.tree_node_from_event(event):
            if event.button == LEFT:
                self.selected = node.data.nodeid
            elif event.button == RIGHT:
                self._toggle_tree_selection(node)

    def render_label(
            self, node: TreeNode[TreeNodeData], base_style: Style,
            style: Style,
        ) -> Text:
        """Render a label for the given node.

        :node:       A tree node.
        :base_style: The base style of the widget.
        :style:      The additional style for the label.
        :return:     A Rich Text object containing the label.
        """
        data = node.data
        node_label = node.label.copy()
        node_label.stylize(style)
        parts = []
        if node.allow_expand:
            #: symbol = '▼ ' if node.is_expanded else '▶ '
            symbol = '⊟ ' if node.is_expanded else '⊞ '
            parts.append((symbol, base_style + TOGGLE_STYLE))
        if data.select_state == SelectionState.SELECTED:
            mark = '▶', Style(color='green')
        elif data.select_state == SelectionState.PARTIALLY_SELECTED:
            mark = '▷', Style(color='green')
        else:
            mark = '⊘', Style(color='red')
        parts.append(mark)
        return Text.assemble(*parts, ' ', node_label)

    def action_select_cursor(self) -> None:
        """Cause a select event for the target node."""
        if self.undo_select is not None:
            self.cursor_line = self.undo_select
            self.undo_select = None
        else:
            super().action_select_cursor()

    def _update_ancestry_completeness(self, node: TreeNode):
        """Work out if all of a node's ancectors are fully selected."""
        def update(node, state):
            if node.data.select_state != state:
                node.data.select_state = state
                self.dirty_nodes.add(node)

        parent = node.parent
        if parent:
            selected = [
                c for c in parent.children
                if c.data.select_state == SelectionState.SELECTED]
            if len(selected) == len(parent.children):
                update(parent, SelectionState.SELECTED)
            elif len(selected) == 0:
                update(parent, SelectionState.UNSELECTED)
            else:
                update(parent, SelectionState.PARTIALLY_SELECTED)
            self._update_ancestry_completeness(parent)

    def _set_recursive_selection(self, node: TreeNode, state: SelectionState):
        """Set the selection state for a node and all its decendents."""
        for child in node.children:
            self._set_recursive_selection(child, state=state)
        data = node.data
        if data.select_state != state:
            data.select_state = state
            self.dirty_nodes.add(node)
            if data.nodeid:
                self.post_message(TestSelectEvent(
                    data.nodeid,
                    selected=data.select_state == SelectionState.SELECTED))

    def set_test_selection(self, nodeid: str, *, selected: bool) -> None:
        """Set the selected state of a test (leaf) node.

        This has no effect if the leaf node's state does not need to change.

        :nodeid:   The pytest nodeid.
        :selected: True if the test should be selected.
        """
        tree_node = self.get_node_by_pytest_nodeid(nodeid)
        data = tree_node.data
        if selected:
            wanted = SelectionState.SELECTED
        else:
            wanted = SelectionState.UNSELECTED
        print(
            'TEST_TREE.set_test_selection', nodeid, wanted, data.select_state)
        if data.select_state != wanted:
            self._toggle_tree_selection(tree_node)


class CompactSelect(Select):
    """The standard ``Select`` widget configured for height == 1."""

    DEFAULT_CSS = '''
        CompactSelect {
            border: none;

            &:focus SelectCurrent {
                border: none;
                padding: 0 1;
            }
            &:blur SelectCurrent {
                border: none;
                padding: 0 1;
            }
            & SelectCurrent {
                border: none;
                padding: 0 1;
            }
            & SelectOverlay:focus {
                border: none;
                padding: 0;
            }
        }
    '''


class CompactButton(Button):
    """A button with styling that makes it compact.

    This is the only way I have found to achieve the effect.
    """

    DEFAULT_CSS = '''
        CompactButton {
            width: auto;
            padding: 0 0 0 0;
            min-width: 16;
            height: auto;
            background: $panel;
            color: $text;
            border: none;
            border-top: wide $panel-lighten-2;
            border-left: wide $panel-lighten-2;
            border-bottom: wide $panel-darken-2;
            border-right: wide $panel-darken-2;
            text-align: left;
            content-align: center middle;
            text-style: none;
        }
        CompactButton:focus {
            background: $panel;
            text-style: none;
        }
        CompactButton:hover {
            border-top: wide $panel-lighten-2;
            text-style: bold;
            background: $panel;
        }
        CompactButton.-active {
            background: $panel;
            border-top: wide $panel-darken-2;
            border-left: wide $panel-darken-2;
            border-bottom: wide $panel-lighten-2;
            border-right: wide $panel-lighten-2;
        }
    '''

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.active_effect_duration = 0.15


class ShortButton(Button):
    """A short (1 line tall) button with some 3D styling."""

    DEFAULT_CSS = '''
        ShortButton {
            width: auto;
            padding: 0 0 0 0;
            min-width: 10;
            height: auto;
            background: $panel;
            color: $text;
            border: none;
            border-left: wide $panel-lighten-2;
            border-right: wide $panel-darken-2;
            text-align: left;
            content-align: center middle;
            text-style: none;
        }
        ShortButton:focus {
            background: $panel;
            text-style: none;
        }
        ShortButton:disabled {
            border: none;
            border-left: wide $panel-lighten-2;
            border-right: wide $panel-darken-2;
        }
        ShortButton:enabled {
            border: none;
            border-left: wide $panel-lighten-2;
            border-right: wide $panel-darken-2;
        }
        ShortButton:hover {
            border: none;
            border-left: outer $panel-lighten-2;
            border-right: outer $panel-darken-2;
            text-style: bold;
            background: $panel;
        }
        ShortButton.-active {
            background: $panel;
            border-left: wide $panel-darken-2;
            border-right: wide $panel-lighten-2;
        }
    '''

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.active_effect_duration = 0.15


class FlatButton(Button):
    """A button with styling that makes it compact and flat looking."""

    DEFAULT_CSS = '''
        FlatButton {
            border: none;
            margin: 0 0 0 1;
            height: 1;
        }
        FlatButton:focus {
            border: none;
        }
        FlatButton:hover {
            border: none;
        }
    '''


class MiniButton(Label):
    """A label that acts as a small button."""

    class Pressed(Message):
        """Signal that user has pressed this button."""

        def __init__(self, button: MiniButton):
            super().__init__()
            self.button = button

        @property
        def control(self):
            """The button that was pressed."""
            return self.button

    DEFAULT_CSS = '''
        MiniButton{
            width: 1;
            height: 1;
        }
        MiniButton:hover {
            background: $primary;
        }
        MiniButton:focus {
            background: $primary;
        }
    '''

    def __init__(self, label: str, **kwargs):
        super().__init__(renderable=label, **kwargs)
        self.can_focus = True

    @property
    def label(self):
        """The renderable providing the button's text."""
        return self.renderable

    @on(Click)
    def emit_pressed(self, event: Click):
        """Emit a Pressed message in response to a user's mouse click."""
        self.post_message(self.Pressed(self))


class IntInput(Input):
    """An input widget for integer values."""

    def __init__(self, *args, **kwargs):
        validate_on = kwargs.pop('validate_on', ['changed'])
        validators = kwargs.pop('validators', [])
        for validator in validators:
            if isinstance(validator, Number):
                break
        else:
            validators.append(Number())
        super().__init__(
            *args, validate_on=validate_on, validators=validators, **kwargs)

    @property
    def int_value(self) -> int:
        """The widget's value conbverted to an integer."""
        return safe_int(self.value)


#
# Controls linked to configuration values.
#
class ConfigCompactSelect(ConfigurationItemReflector, CompactSelect):
    """A compact selection widget that displays a configuration item."""

    # pylint: disable=too-many-ancestors
    def __init__(self, *, item: Item, **kwargs):
        super().__init__(item=item, **kwargs)


class ConfigCheckbox(ConfigurationItemReflector, Checkbox):
    """A check box that displays a configuration item."""

    def __init__(self, *, item: Item, **kwargs):
        super().__init__(item=item, **kwargs)


class ConfigInput(ConfigurationItemReflector, Input):
    """An input tied to the configuration."""

    DEFAULT_CSS = '''
        ConfigInput {
            border: none;
        }
        ConfigInput:focus {
            border: none;
        }
    '''

    def __init__(self, *, item: Item, **kwargs):
        super().__init__(item=item, **kwargs)


class ConfigIntInput(ConfigurationItemReflector, IntInput):
    """An integer input tied to the configuration."""

    DEFAULT_CSS = '''
        ConfigIntInput {
            border: none;
        }
        ConfigIntInput:focus {
            border: none;
        }
    '''

    def __init__(self, *, item: Item, **kwargs):
        super().__init__(item=item, **kwargs)


class SpinButton(MiniButton):
    """A MiniButton that steps a value."""

    def __init__(self, step: Literal[-1, 1], **kwargs):
        text = '▲' if step == 1 else '▼'
        super().__init__(text, **kwargs)
        self.step = step

    @on(Key)
    def handle_key_click(self, event: Key) -> None:
        """Update the target widget upon enter or space key."""
        if event.key in ('enter', 'space'):
            self.parent.handle_button_press(self)

    # TODO: Should refactor to use the Pressed message.
    @on(Click)
    def handle_click(self, event: Click):
        """Handle user click on on this button."""
        self.parent.handle_button_press(self)


class SpinControl(Widget):
    """A numeric input with up and down adjustment buttons.

    :item:   The configuration Item instance.
    :label:  The control's label.
    :value:  TODO: Not required.
    :limits: An range defining the value's limits.
    """

    DEFAULT_CSS = '''
        SpinControl {
            height: 1;
            width: auto;
            layout: horizontal;
        }
        SpinControl .input_label {
            padding: 0 1 0 0;
            width: auto;
        }
        SpinControl ConfigInput {
            padding: 0 0 0 0;
            border: none;
            color: $secondary;
            min_width: 3;
        }
        SpinControl ConfigInput.-invalid {
            border: none;
        }
        SpinControl ConfigInput:focus {
            border: none;
        }
        SpinControl SpinButton{
            color: $secondary;
        }
        SpinControl .down_button {
            margin: 0 1 0 0;
        }
    '''

    def __init__(self,
            item: Item,
            label: str, value: str, limits: range, **kwargs):
        classes = f'{kwargs.pop("classes", "")}'.strip()
        super().__init__(classes=classes, **kwargs)
        self.label = label
        self.item = item
        self.limits = limits

    def compose(self) -> Iterator[Widget]:
        """Build the widget hierarchy."""
        if self.label:
            yield Label(self.label, classes='input_label')
        yield SpinButton(step=1, classes='up_button')
        yield SpinButton(step=-1, classes='down_button')
        yield ConfigIntInput(item=self.item,
            validate_on=['changed'],
            validators=[Number(self.limits.start, self.limits.stop - 1)],
        )

    @property
    def value(self):
        """The currently entered value."""
        field = self.query_one(Input)
        return safe_int(field.value, self.limits.start)

    def handle_button_press(self, button: SpinButton) -> None:
        """React to a sping button activation."""
        field = self.query_one(Input)
        v = safe_int(field.value) + button.step
        res = field.validate(str(v))
        if res and res.is_valid:
            field.value = str(v)


class LabelledConfigInput(Widget):
    """A labeled field for a configuration string."""

    DEFAULT_CSS = '''
        LabelledConfigInput {
            layout: horizontal;
            height: 3;
            width: auto;
        }
        LabelledConfigInput Input {
            height: 1;
            width: 20;
        }
    '''

    def __init__(self,
            item: Item, label: str, **kwargs):
        classes = f'{kwargs.pop("classes", "")}'.strip()
        self.input_classes = f'{kwargs.pop("input_classes", "")}'.strip()
        super().__init__(classes=classes, **kwargs)
        self.label = label
        self.item = item

    def compose(self) -> Iterator[Widget]:
        """Build the widget hierarchy."""
        if self.label:
            yield Label(self.label, classes='input_label')
        yield ConfigInput(item=self.item, classes=self.input_classes)


class Resizeable:
    """Support for resizing a widget with a border."""

    def init_resizer_part(self):
        self._resizer: HeightResizer | None = None

    def on_mouse_down(self, event: MouseEvent):
        """Prepare to resize as the user drags the mouse."""
        x, y = event.screen_x, event.screen_y
        if is_in_top_line(self.region, x, y) and not self._resizer:
            self.app.post_message(HIDEvent(
                name=f'L-down:top-border[{self.id}]'))
            self._resizer = HeightResizer(self, y, self.calc_height)
            self.capture_mouse(capture=True)
            event.stop()

    def on_mouse_move(self, event: MouseEvent):
        """Adjust the size as a result of mouse movement."""
        if self._resizer:
            self.app.post_message(HIDEvent(
                name=rf'Drag:border\[{self.id}]'))
            self._resizer.update(event)
            event.stop()

    def on_mouse_up(self, event: MouseEvent):
        """Cease a resizing operation."""
        if self._resizer:
            self.app.post_message(HIDEvent(name='Release'))
            self._resizer = None
            self.capture_mouse(capture=False)

    def calc_height(self, prev: int, new: int) -> int:
        """Calculate allowed height given a previous and desired height."""
        # pylint: disable=no-self-use
        return new


class HeightResizer:
    """A manager for adjusting a widget's height."""

    # pylint: disable=too-few-public-methods
    def __init__(
            self, w: Widget, y: int,
            calc_height: Callable[[int, int], int]):
        self.w = proxy(w)
        region = self.w.region
        self.base_y = y
        self.base_height = int(region.height)
        self.prev_height = self.base_height
        self.calc_height = calc_height

    def update(self, event: MouseEvent):
        """Change widget height in response to a mouse movement."""
        delta = self.base_y - event.screen_y
        new_height = self.calc_height(
            self.prev_height, self.base_height + delta)
        if new_height != self.prev_height:
            self.w.styles.height = new_height
            self.prev_height = new_height


def is_in_top_line(r: Region, x, y):
    """Test whether a point line in the top line of a region."""
    return r.x <= x < r.x + r.width and r.y == y
