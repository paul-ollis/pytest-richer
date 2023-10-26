"""Controller for the configuration panel."""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Button, TabPane, Tree

from .configuration import ConfigItemChangeTracker, Item, ItemSpec
from .control import Controller
from .widgets import ConfigCheckbox, LabelledConfigInput, SpinControl

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .configuration import ConfigTuple, Item


class ConfigWidget(Widget):
    """Base for configuration value widgets."""

    DEFAULT_CSS = '''
        ConfigWidget {
            height: auto;
            layout: horizontal;
            border: solid $primary-lighten-3;
            border_subtitle_align: left;
            margin: 0 0 1 0;
        }
        ConfigWidget .config_value {
            height: auto;
            border: round;
        }
    '''

    def __init__(self, user: ConfigTuple, project: ConfigTuple, **kwargs):
        super().__init__(
            id=f'config--{user.item.cname}', classes='config_pane_items',
            **kwargs)
        self.user = user
        self.project = project
        self.user.item.register_for_changes(self)
        self.project.item.register_for_changes(self)

    def on_mount(self):
        """Finish setup up now that the widget hierarchy exists."""
        self.handle_config_change(self.user.item)
        self.border_title = self.user.item.cname.replace('-', ' ─▷ ')
        spec = self.user.spec
        if spec.min_val and spec.max_val:
            self.border_subtitle = f'range: {spec.min_val}..{spec.max_val}'

    @on(Button.Pressed)
    def handle_button(self, _event: Button.Pressed) -> None:
        """Copy project value to user value."""
        self.user.item.value = self.project.item.value

    def handle_config_change(self, item: Item) -> None:
        """Respond to a change of an item's value."""
        try:
            b = self.query_one(Button)
        except NoMatches:
            pass
        else:
            b.disabled = self.user == self.project


class BoolConfigWidget(ConfigWidget):
    """Config control widget for a boolean value."""

    def compose(self) -> Iterator[Widget]:
        """Build the widget hierarchy."""
        yield ConfigCheckbox(
            label='User', item=self.user.item,
            classes=f'config_value {self.user.item.class_name}')
        yield Button(
            '<--', variant='primary', classes='config_copy_button')
        yield ConfigCheckbox(
            label='Project', item=self.project.item,
            classes=f'config_value {self.project.item.class_name}')


class IntConfigWidget(ConfigWidget):
    """Config control widget for an integer value."""

    def compose(self) -> Iterator[Widget]:
        """Build the widget hierarchy."""
        spec = self.user.spec
        limits = range(spec.min_val, spec.max_val + 1)
        yield SpinControl(
            self.user.item, label='User: ', value=0, limits=limits,
            classes='config_value')
        yield Button(
            '<--', variant='primary', classes='config_copy_button')
        yield SpinControl(
            self.project.item, label='Project: ', value=0, limits=limits,
            classes='config_value')


class StrConfigWidget(ConfigWidget):
    """Config control widget for a string value."""

    DEFAULT_CSS = '''
        StrConfigWidget {
            height: 5;
        }
    '''

    def compose(self) -> Iterator[Widget]:
        """Build the widget hierarchy."""
        classes = 'config_value'
        input_classes = 'simple_input'
        yield LabelledConfigInput(
            item=self.user.item,
            label='User: ', classes=f'{classes} {self.user.item.class_name}',
            input_classes=input_classes)
        yield Button(
            '<--', variant='primary', classes='config_copy_button')
        yield LabelledConfigInput(
            item=self.project.item,
            label='Project: ',
            classes=f'{classes} {self.project.item.class_name}',
            input_classes=input_classes)


class ConfigPanel(TabPane):
    """Panel for the overview pane."""

    DEFAULT_CSS = '''
        ConfigPanel {
            layout: vertical;
        }
        ConfigPanel Input {
            height: 1;
            width: 3;
            border: none;
            padding: 0 0 0 0;
            color: $secondary;
        }
        ConfigPanel Button {
            height: 1;
            min-width: 5;
            margin: 1 0 0 0;
            border: none;
        }
        ConfigPanel Tree {
            width: auto;
        }
    '''

    def __init__(self, control: ConfigController):
        super().__init__('Config', id='config_pane')
        self.control = control

    def compose(self) -> ComposeResult:
        """Build up the widget tree for the configuration pane."""
        config = self.control.config
        with Horizontal():
            yield (tree := Tree(label='<All>', id='config_tree_view'))
            tree.show_root = True
            tree.root.expand()
            tree.root.allow_expand = False

            # Create a widget set for every configuration item.
            with VerticalScroll(id='config_list'):
                user_tuples = config.walk_tuples('user')
                project_tuples = config.walk_tuples('project')
                for items in zip(user_tuples, project_tuples):
                    if w := self.create_config_widget(*items):
                        yield w
        config.populate_tree(tree.root)

    @staticmethod
    def create_config_widget(
            user: ConfigTuple, project: ConfigTuple) -> Widget | None:
        """Create a suitable widget for a given configuration item."""
        _item: Item
        spec: ItemSpec
        _item, spec = user
        if spec.non_generic:
            return None
        elif spec.value_type is bool:
            return BoolConfigWidget(user, project)
        elif spec.value_type is int:
            return IntConfigWidget(user, project)
        elif issubclass(spec.value_type, enum.IntEnum):
            return IntConfigWidget(user, project)
        elif spec.value_type is str:
            return StrConfigWidget(user, project)
        else:
            return None


class ConfigController(Controller):
    """Controller for the configuration."""

    def compose(self) -> Iterator[Widget]:
        """Build up the widget tree for the configuration pane."""
        yield ConfigPanel(control=self)

    def handle_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Process selection fo a tree node."""
        if event.control.id == 'config_tree_view':
            node = event.node
            s = [str(node.label)]
            while node.parent:
                node = node.parent
                s.append(str(node.label))
            name = '-'.join(reversed(s[:-1]))
            prefix = f'config--{name}'
            widgets = self.app.query('.config_pane_items')
            for w in widgets:
                if w.id.startswith(prefix):
                    w.display = True
                else:
                    w.display = False
