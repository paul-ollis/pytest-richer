"""Configuration management.

Usage
=====

Import and initialisation
-------------------------

It is recommended that a plain module import (e.g. import configuration) is
used because this module provides some member's with fairly generic names (e.g.
Item and Node).

The module must be explicitly initialised as shown in the following example:
:<py>:

    from pathlib import Path

    model = {
        # See later for format.
    }
    app_name = 'spiffy'
    project_path = Path('.')
    config = configuration.init(app_name, project_path, model)

This does the following:

1. If it does not already exist then this creates a user level configuration
   filled with default values from the model. On Unix-like platforms this will
   typically be in (for our example) $HOME/.config/spiffy/config.json.

   If the file does exist then it is updated as necessry to add missing model
   entries and remove any entries that are nolonger part of the model.

2. If it does not exist, also create a project specific config file, which (for
   out example) will be in ./.spiffy.json. As for the user level file, an
   existing file is updated to frlected any model changes.

The returned `Config` instance is (currently) a singleton, which can be
thereafter obtained by:<py>:

    config = configuration.config()

This may well change in the future, so it is better to avoid the `config`
function and any assumptions about the existence of a singleton.


The model
---------

The model dictionary provides something like a template for the JSON file used
to store the configuration. It is a dictionary where each key is a string
naming a configuration item and each value one of:

- A definition of the configuration item.
- Another dictionary defining sub-items.

For example:<py>:

    config_model = {
        'autosave': ItemSpec(default=False),
        'show': {
            'errors': ItemSpec(default=True),
            'warnings': ItemSpec(default=False),
        },
    }

This example defines three boolean configuration items ``autosave`,
``show-errors`` and ``show-warnings``. The JSON in the saved configuration file
will look like::

    {
        "autosave": false,
        "show": {
            "errors": true,
            "warnings": false
        }
    }

See `ItemSpec` for details of the kinds of configuration values supported.


Configuration values
--------------------

Values are read from the configuration using the `lookup_item` method and
modified using the `set` method. Both methods uses canonical item names, for
example 'show-errors' (given in an earlier example). Note that the dash
character is used as a separator rather than the dot (full stop) for a very
good reason (which I cannot recall).

Examples of reading and setting values are:<py>:

    item: configuration.Item

    # Get the project specific value for show-errors.
    item = config.lookup_item('show-errors')
    print(item.value)

    # Get the same value, but from the user's config file.
    item = config.lookup_item('show-errors', type_name='user')

    # Set the value at the project level.
    config.set('show-errors', value=False)

    # Set the value in the user's config file.
    config.set('show-errors', value=False, type_names=('user',))

    # Set the value both at the project level and in user's config file.
    config.set('show-errors', value=False, type_names=('project', 'user'))

    # Alternatively, you can set the value of a looked up item.
    item = config.lookup_item('show-errors', type_name='user')
    item.value = False

Notice that `lookup_item` returns an `Item` rather than a simple value. An
`Item` provides a `value` property which can be read and set. It also provides
a `register_for_changes` method. For a given dashed_name and type_name, the
lookup_item method will always return the same `Item` instance.


Responding to config value changes
----------------------------------

The `Item.register_for_changes` allows an object to be informed when that
item's value is changed.
:<py>:

    item.value = 20
    item.register_for_changes(obj)
    item.value = 42                 # obj.handle_config_change is called.
    item.value = 42                 # value is unchanged, no call is made.

The object ``obj`` must support the `ConfigItemChangeTracker` protocol by
providing a `handle_config_change` method.


Saving changes
--------------

Changes to any `Item` are normally automatically written to the configuration
file. A number of configuration changes can be buffered and applied as a group
using the `Config.block_update` context manager:<py>:

    with config.block_update(type_names=('user', 'project'))
        # Set multiple items at the project and user level.
        ...
    # Now all the changes will be saved (one write per file).
"""
from __future__ import annotations

import enum
import json
import subprocess
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, NamedTuple, Protocol, TYPE_CHECKING, TypeVar
from weakref import proxy

import appdirs

if TYPE_CHECKING:
    from collections.abc import Iterator

    from textual.widgets.tree import TreeNode

ConfigValueType = TypeVar('ConfigValueType', bound=bool|int|str)


class ConfigIntEnum:

    @classmethod
    def from_str_intval(cls, value: str) -> AutoRunMode:
        v = int(value)
        for name, enum_const in cls.__members__.items():
            if value == name or value == enum_const.value:
                return enum_const
        for v in cls:
            return v


class AutoRunMode(ConfigIntEnum, enum.IntEnum):
    ALL = 0
    ALL_FAILING = 1
    SELECTED_FAILING = 2


@dataclass
class ItemSpec:
    """The specification of a configuration item.

    :@default:
        The default value for this item.
    :@value_type:
        The type used to store this value. If not provided then the type is
        inferred from the `default`. If provided then it should be a callable
        that takes a single value and returns a value of a suitable type.
    :@min_val:
        A minimum permitted value for this item. It defaults to ``None`` to
        indicate that there is no minimum.
    :@max_val:
        A maximum permitted value for this item. It defaults to ``None`` to
        indicate that there is no maximum.
    :@non_generic:
        Set this if the item should not, for example, be automatically shown
        in a conifiguration tool.
    """

    default: bool | int | str | enum.Enum
    value_type: bool | int | None = None
    min_val: int  | None = None
    max_val: int  | None = None
    non_generic: bool = False

    def __post_init__(self):
        if self.value_type is None:
            self.value_type = type(self.default)

    def convert(self, value):
        """Convert the given value to this item's defined value type."""
        if issubclass(self.value_type, enum.Enum):
            for name, enum_const in self.value_type.__members__.items():
                if value == name or value == enum_const.value:
                    return enum_const
            if isinstance(value, str):
                # TODO: This only allows for integer values in string form.
                #       What about the enumeration names?
                return self.value_type.from_str_intval(value)

        return self.value_type(value)


_model = {
    'run' : {
        'dark_mode': ItemSpec(default=True),
        'test_dir': ItemSpec(default='tests'),

        # This provides a default value for a first run. Following code adds
        # watch_01,... to watch_19. All these entries are marked as non-generic
        # so that they do not appear in the configuration panel.
        'watch_00': {
            'path': ItemSpec(default='tests', non_generic=True),
            'recurse': ItemSpec(default=True, non_generic=True),
            'patterns': ItemSpec(default='*.py', non_generic=True),
        },

        'auto_run_mode': ItemSpec(
            default=AutoRunMode.ALL, min_val=0, max_val=len(AutoRunMode)),
        'jump_to_details': ItemSpec(default=True),
    },
    'overview': {
        'show': {
            'failures': ItemSpec(default=True),
            'warnings': ItemSpec(default=False),
            'logging': ItemSpec(default=False),
        },
        'std_symbols': ItemSpec(default=False),
    },
    'postmortem': {
        'show': {
            'tree': ItemSpec(default=False),
        },
        'details': {
            'locals': ItemSpec(default=False),
            'context': ItemSpec(default=False),
            'stdout': ItemSpec(default=False),
            'stderr': ItemSpec(default=False),
            'logging': ItemSpec(default=False),
            'simple_stack': ItemSpec(default=False),
            'max_container_length': ItemSpec(3, min_val=1, max_val=20),
            'max_string_length': ItemSpec(40, min_val=1, max_val=256),
            'context_length': ItemSpec(2, min_val=1, max_val=15),
        },
    },
    'collection': {
        'details': {
            'locals': ItemSpec(default=False),
            'context': ItemSpec(default=False),
            'stdout': ItemSpec(default=False),
            'stderr': ItemSpec(default=False),
            'logging': ItemSpec(default=False),
            'simple_stack': ItemSpec(default=False),
            'max_container_length': ItemSpec(3, min_val=1, max_val=20),
            'max_string_length': ItemSpec(40, min_val=1, max_val=256),
            'context_length': ItemSpec(2, min_val=1, max_val=15),
        },
    },
}
# TODO: Nothing enforces the limit of 20. Is that a problem or will the code
#       elegantly cope?
for n in range(1, 20):
    _model['run'][f'watch_{n:02}']= {
        'path': ItemSpec(default='', non_generic=True),
        'recurse': ItemSpec(default=True, non_generic=True),
        'patterns': ItemSpec(default='', non_generic=True),
    }


class PytestCapabilities:
    """Information about pytest's capabilities.

    This uses output from 'pytest --help' to heuristically determine various
    capabilities provided by pytest and installed plugins. Capabilities are
    provided by lazily evaluated properties.
    """

    def __init__(self):
        res = subprocess.run(
            ['pytest', '--help'],                                  # noqa: S607
            capture_output=True, encoding='utf-8',
            text=True, check=False)
        self._help_text = res.stdout

    @property
    def has_xdist(self) -> bool:
        """True if the pytest-xdist plugin is available."""
        return self._bool_prop_by_line(
            'distributed and subprocess testing:', '_has_xdist')

    @property
    def has_coverage(self) -> bool:
        """True if the pytest-cov plugin is available."""
        return self._bool_prop_by_line(
            'coverage reporting with distributed testing support:',
            '_has_coverage')

    @property
    def has_syrupy(self) -> bool:
        """True if the syrupy plugin is available."""
        return self._bool_prop_by_line('syrupy:', '_has_syrupy')

    def _bool_prop_by_line(self, text: str, name: str) -> bool:
        if (v := getattr(self, name, None)) is None:
            v = self._help_contains_line(text)
            setattr(self, name, v)
        return v

    def _help_contains_line(self, text: str) -> bool:
        for line in self._help_text.splitlines():
            if line.rstrip() == text:
                return True
        return False


class ConfigItemChangeTracker(Protocol):
    """Protocol for objects that track a config item's changes."""

    def handle_config_change(self, item: Item) -> None:
        """Respond to a change of an item's value."""


class Item(Generic[ConfigValueType]):
    """A value extracted from the configuration.

    :@parent:
        The parent `Item` or ``None``.
    :@name:
        The stem name of this item. The `cname` property gives the fully
        qualified canonical name.
    :@spec:
        The `ItemSpec` for this item.
    """

    def __init__(
            self, parent: Node, name: str, value: ConfigValueType,
            spec: ItemSpec):
        self.parent = proxy(parent) if parent else None
        self.name = name
        self.change_trackers: list[weakref.ref[ConfigItemChangeTracker]] = []
        self._value = value
        self.spec = spec

    @property
    def value(self):
        """The value of this item."""
        return self._value

    @value.setter
    def value(self, value):
        value = self.spec.convert(value)
        if self.value != value:
            self._value = value
            self.parent.save()
            self._notify_changer_trackers()

    @property
    def cname(self) -> str:
        """The canonical, dashed name for this node."""
        return f'{self.parent.cname}-{self.name}'

    @property
    def class_name(self) -> str:
        """The Textual class name used to identify this value."""
        return f'{self.parent.type_name}__config__{self.cname}'

    def register_for_changes(self, tracker: ConfigItemChangeTracker) -> None:
        """Register to be notified of changes to this item."""
        self.change_trackers.append(weakref.ref(tracker))

    def _notify_changer_trackers(self) -> None:
        """Notify registered trackers about a change to this item."""
        dead = set()
        for ref in self.change_trackers:
            if (tracker := ref()) is None:
                dead.add(ref)
            else:
                tracker.handle_config_change(self)
        if dead:
            self.change_trackers[:] = [
                r for r in self.change_trackers if r not in dead]

    def __eq__(self, other: Item):
        return self.value == other.value


class ConfigTuple(NamedTuple):
    """The tuples of values stored in the config hierarchy leaf Nodes."""

    item: Item
    spec: ItemSpec


class Node:
    """A node within a configuration.

    @parent:   The parent Node or `RootNode`.
    @name:     The simple name for this node.
    @children: A list of child nodes.
    @values:
        A dictionary of name, `ConfigTuple` pairs for actual values at this
        node's level.
    """

    type_name: str = ''

    def __init__(
            self, parent: Node,
            name: str,
            d: [str, tuple[ConfigValueType, ItemSpec] | dict],
        ):
        self.parent = proxy(parent)
        self.name = name
        self.children: dict[str, Node] = {}
        self.values: dict[str, ConfigTuple] = {}
        for child_name, dict_or_tuple in d.items():
            if isinstance(dict_or_tuple, dict):
                self.children[child_name] = Node(
                    self, child_name, dict_or_tuple)
            else:
                value, spec = dict_or_tuple
                tup = ConfigTuple(
                    Item(self, child_name, spec.convert(value), spec), spec)
                self.values[child_name] = tup

    @property
    def cname(self) -> str:
        """The canonical, dashed name for this node."""
        if self.parent and self.parent.name:
            return f'{self.parent.cname}-{self.name}'
        else:
            return self.name

    @property
    def type_name(self) -> str:
        """The type name for this configuration hierarchy."""
        return self.parent.type_name

    def as_dict(self) -> dict[str, dict | ConfigValueType]:
        """Convert this node to a nested dictionary."""
        d = {}
        for name, value in self.values.items():
            d[name] = value.spec.convert(value.item.value)
        for name, value in self.children.items():
            d[name] = value.as_dict()
        return d

    def lookup_item(self, dashed_name) -> Item:
        """Lookup a config Item using its canonical name."""
        return self.lookup_tuple(dashed_name).item

    def lookup_tuple(self, dashed_name) -> ConfigTuple:
        """Lookup a ConfigTuple using its canonical name."""
        name, _, branch = dashed_name.partition('-')
        if branch:
            return self.children[name].lookup_tuple(branch)
        else:
            return self.values[name]

    def lookup_section(self, dashed_name) -> ConfigTuple:
        """Lookup a section of the configuration using its canonical name."""
        name, _, branch = dashed_name.partition('-')
        if branch:
            return self.children[name].lookup_tuple(branch)
        else:
            return self.children[name]

    def set(self, dashed_name, value: ConfigValueType):            # noqa: A003
        """Set a value using its canonical name.

        :return: True if the value was changed.
        """
        name, _, branch = dashed_name.partition('-')
        if branch:
            self.children[name].set(branch, value)
        else:
            self.values[name].item.value = value

    def walk_tuples(self) -> Iterator[ConfigTuple]:
        """Walk the tree yielding each configuration item."""
        yield from self.values.values()
        for node in self.children.values():
            yield from node.walk_tuples()

    def populate_tree(self, tree_node: TreeNode):
        """Populate a tree widget node."""
        if not self.has_generic_progeny():
            return
        elif self.children:
            sub_tree_node = tree_node.add(
                self.name, expand=True, allow_expand=False)
            for node in self.children.values():
                node.populate_tree(sub_tree_node)
        else:
            tree_node.add_leaf(self.name)

    def save(self):
        """Save this configuration hierarchy."""
        self.parent.save()

    def has_generic_progeny(self) -> bool:
        """Test whether any descendant item is a generic node."""
        if any(not tup.spec.non_generic for tup in self.values.values()):
            return True
        for node in self.children.values():
            if node.has_generic_progeny():
                return True
        return False


class RootNode(Node):
    """The root of a configuration hierarchy."""

    def __init__(
            self, parent: Config, type_name: str,
            d: [str, ConfigValueType | dict],
        ):
        super().__init__(parent=self, name='', d=d)
        self.parent = proxy(parent)
        self._type_name = type_name

    @property
    def type_name(self) -> str:
        """The type name for this configuration hierarchy."""
        return self._type_name

    def populate_tree(self, tree_node: TreeNode):
        """Populate a tree widget node."""
        for node in self.children.values():
            node.populate_tree(tree_node)

    def save(self):
        """Save this configuration hierarchy."""
        self.parent.save_type(self.type_name)


class Config:
    """Configuration loading, saving, lookup and layering.

    @configs:
        A dictionary of Path, `RootNode` pairs. The key may be 'user' or
        'project'.
    """

    inst: Config | None = None

    def __init__(self, project_path: Path):
        self._pytest = PytestCapabilities()
        self.configs: dict[str, tuple[Path, RootNode]] = {}
        self._pending_writes: dict[str, int] = {}
        user_conf_path = Path(appdirs.user_config_dir('pytest-richer'))
        conf = self._init_config(user_conf_path/'config.json', _model, 'user')
        self._init_config(
            project_path/'.pytest-richer.json', conf.as_dict(), 'project',
            is_model=False)

    def _init_config(
            self, path: Path, default: dict, type_name: str,
            *, is_model: bool = True,
        ) -> RootNode:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            if is_model:
                self.save(model_to_simple_dict(default), path)
            else:
                self.save(default, path)
        conf = RootNode(self, type_name, self.load(path))
        self.configs[type_name] = path, conf
        self._pending_writes[type_name] = 0
        return conf

    @property
    def pytest(self) -> PytestCapabilities:
        """Information about pytest's capabilities."""
        return self._pytest

    @staticmethod
    def load(path: Path) -> dict:
        """Load a configuration."""
        text = path.read_text(encoding='utf-8')
        return fix(json.loads(text), _model)

    @staticmethod
    def save(d: dict, path: Path) -> None:
        """Save a configuration."""
        path.write_text(
            json.dumps(d, sort_keys=False, indent=4), encoding='utf-8')

    def save_type(self, type_name: str):
        """Save the configuration of a given type."""
        if not self._pending_writes[type_name]:
            path, conf = self.configs[type_name]
            self.save(conf.as_dict(), path)

    def lookup_item(
            self, dashed_name: str, *, type_name: str = 'project') -> Item:
        """Lookup a config value."""
        _, conf = self.configs[type_name]
        return conf.lookup_item(dashed_name)

    def lookup_section(
            self, dashed_name: str, *, type_name: str = 'project') -> Node:
        """Lookup a section of the configuration using its canonical name."""
        _, conf = self.configs[type_name]
        return conf.lookup_section(dashed_name)

    def set(                                                       # noqa: A003
            self, dashed_name: str, *, value: ConfigValueType,
            type_names: tuple[str, ...] = ('project',),
        ) -> None:
        """Set a config value."""
        confs = [self.configs[n] for n in type_names]
        for _, conf in confs:
            conf.set(dashed_name, value)

    def walk_tuples(self, type_name: str = 'project') -> Iterator[ConfigTuple]:
        """Walk the config tree yielding name, value pairs."""
        _, conf = self.configs[type_name]
        yield from conf.walk_tuples()

    def populate_tree(self, tree_node: TreeNode) -> None:
        """Populate a tree widget node."""
        _, conf = self.configs['project']
        conf.populate_tree(tree_node)

    @contextmanager
    def block_update(
            self, type_names: tuple[str, ...] = ('project',),
        ) -> Iterator[None]:
        """Allow a block of config updates before writing.

        :type_names:
            A tuple containing either or both of 'user, 'project'.
        """
        for name in type_names:
            self._pending_writes[name] += 1
        try:
            yield
        finally:
            for name in type_names:
                self._pending_writes[name] -= 1
                self.save_type(name)


def init(project_path: Path) -> None:
    """Initialise the Config, creating and loading as necessary."""
    if not Config.inst:
        Config.inst = Config(project_path)


def config() -> Config:
    """Get the Config instance."""
    return Config.inst


def _navigate_to_leaf(
        conf: dict, dashed_name: str) -> tuple[dict, str]:
    """Navigate configuration to the node holding leaf of dashed_name.

    :return: A tuple of the leaf containing node and the leaf name.
    """
    *parents, leaf = dashed_name.split('-')
    node = conf
    for parent in parents:
        node = node[parent]
    return node, leaf


def new_fix(node: dict, model: dict) -> bool:
    """Fix a node within a loaded configuration.

    Missing keys are added and defunct keys are removed.

    :return: True is any fixes were applied.
    """
    # Drop any entries that do not appear in the model.
    changed = False
    for key in list(node):
        if key not in model:
            node.pop(key)
            changed = True

    # Add defaults for any model items that are not present.
    for key, spec_or_dict in model.items():
        if key not in node:
            changed = True
            if isinstance(spec_or_dict, dict):
                node[key] = model_to_simple_dict(spec_or_dict)
            else:
                node[key] = spec_or_dict.default

    # Convert simple values to value, spec tuples.
    for key, spec_or_dict in model.items():
        if not isinstance(spec_or_dict, dict):
            changed = True
            node[key] = node[key], spec_or_dict

    # Rebuild dict to match the order in the model.
    old_node = node.copy()
    node.clear()
    node.update({name: old_node[name] for name in model})

    # Recurse.
    for key, value in node.items():
        if isinstance(value, dict):
            if fix(value, model[key]):
                changed = True
    return changed


def fix(node: dict, model: dict):
    """Fix a node within a loaded configuration.

    Missing keys are added and defunct keys are removed.
    """
    # Drop any entries that do not appear in the model.
    for key in list(node):
        if key not in model:
            node.pop(key)

    # Add defaults for any model items that are not present.
    for key, spec_or_dict in model.items():
        if key not in node:
            if isinstance(spec_or_dict, dict):
                node[key] = model_to_simple_dict(spec_or_dict)
            else:
                node[key] = spec_or_dict.default

    # Convert simple values to value, spec tuples.
    for key, spec_or_dict in model.items():
        if not isinstance(spec_or_dict, dict):
            node[key] = node[key], spec_or_dict

    # Rebuild dict to match the order in the model.
    old_node = node.copy()
    node.clear()
    node.update({name: old_node[name] for name in model})

    # Recurse.
    for key, value in node.items():
        if isinstance(value, dict):
            fix(value, model[key])
    return node


def model_to_simple_dict(d: dict):
    """Convert a nested dictionary model to a one with simple values."""
    ret = {}
    for name, value in d.items():
        if isinstance(value, dict):
            ret[name] = model_to_simple_dict(value)
        else:
            ret[name] = value.default
    return ret
