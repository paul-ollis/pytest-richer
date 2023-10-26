"""Controller for the file watching configuration panel."""
from __future__ import annotations
# pylint: disable=too-many-ancestors

import asyncio
from functools import partial
from itertools import filterfalse
from pathlib import Path, PurePath
from typing import NamedTuple, TYPE_CHECKING
from weakref import proxy

from textual import on
from textual.containers import Horizontal, Vertical
from textual.events import Mount
from textual.message import Message
from textual.widget import Widget
from textual.widgets import (
    Button, Checkbox, DirectoryTree, Input, Static, TabPane)

from . import file_watching
from .control import Controller
from .events import WatchedFilesChanged
from .widgets import CompactButton, MiniButton

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from typing import ClassVar

    from . import app as tui_app, configuration

RuleEntry = tuple[Path, bool, frozenset[str]]

PlainStatic = partial(Static, markup=False)


class Rule(NamedTuple):
    """The watch rule for a single directory.

    @path:
        The ``Path`` of the watched directory.
    @recursive:
        True if patterns are matched recursively within the directory.
    @patterns:
        A ``frozenset`` of file pattern strings.
    """

    path: Path
    recurse: bool
    patterns: frozenset[PurePath]


class FileWatcher(file_watching.FileWatcher):
    """A file_watching.FileWatcher that cooperates with a FileWatchPanel."""

    def __init__(self, control: FileWatchPanel):
        super().__init__()
        self.control = proxy(control)

    def on_modified(self, event: file_watching.FileSystemEvent):
        """Handle a file modification event."""
        self.control.handle_fs_change(event)


class PureDirectoryTree(DirectoryTree):
    """A DirectoryTree that only show directories."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        """Yield only directories."""
        def not_wanted(p: Path) -> bool:
            return not (
                p.is_dir() and not p.name.startswith('.')
                and not p.name.startswith('__'))

        yield from filterfalse(not_wanted, paths)


class GlobEntry(Widget):
    """A single entry for a glob pattern."""

    DEFAULT_CSS = '''
        GlobEntry {
            height: auto;
            width: auto;
            layout: horizontal;
        }
        GlobEntry Horizontal {
            height: 1;
        }
        GlobEntry MiniButton {
            width: 3;
            background: $primary;
        }
        GlobEntry Input {
            height: 1;
        }
    '''

    class Changed(Message):
        """Signal that this glob pattern entry has changed."""

        bubble: ClassVar[bool] = False

    class RemovePressed(Message):
        """Signal that user has pressed this glob entry's remove button."""

        def __init__(self, entry: GlobEntry):
            super().__init__()
            self.entry = entry

    def __init__(self, pattern: str, **kwargs):
        super().__init__(**kwargs)
        self.pattern_path = PurePath(pattern)

    def compose(self) -> Iterator[Widget]:
        """Build the widget hierarchy."""
        with Horizontal():
            yield MiniButton(label=' - ')
            yield Input(value=str(self.pattern_path))

    def set_button_disabled(self, *, flag: bool):
        """Set the disabled state of the button."""
        self.query_one(MiniButton).disabled = flag

    @on(MiniButton.Pressed)
    def emit_pressed(self, event: MiniButton.Pressed) -> None:
        """Send a `RemovePressed` message for this entry."""
        self.post_message(self.RemovePressed(self))
        event.stop()

    @on(Input.Submitted)
    def emit_changed(self, event: Input.Changed) -> None:
        """Post a `Changed` message."""
        self.pattern_path = PurePath(event.value)
        self.post_message(self.Changed())
        self.app.screen.set_focus(None)


class DirectoryEntry(Widget):
    """A single directory entry in a DirectoryListView."""

    DEFAULT_CSS = '''
        DirectoryEntry {
            height: auto;
            width: auto;
            layout: horizontal;
        }
        DirectoryEntry CompactButton {
            min-width: 3;
        }
        DirectoryEntry Checkbox {
            height: 1;
            border: none;
            margin: 1 0 0 4;
            padding: 0 0 0 0;
        }
        DirectoryEntry Checkbox:focus {
            border: none;
        }
        DirectoryEntry Input:focus {
            border: none;
        }
        DirectoryEntry Vertical {
            height: auto;
            margin: 1 0 0 4;
        }
        DirectoryEntry Static.dir_name {
            padding: 0 0 0 0;
        }
        DirectoryEntry Input {
            width: 20;
            padding: 0 0 0 0;
            margin: 0 0 0 2;
            border: none;
        }
        DirectoryEntry Vertical > MiniButton {
            width: 3;
            background: $primary;
        }
    '''

    class Accepted(Message):
        """Signal that user has accepted the current directory choice."""

        bubble: ClassVar[bool] = False

    class RemovePressed(Message):
        """Signal that user has clicked the rmove button for this entry."""

        bubble: ClassVar[bool] = False

        def __init__(self, entry: DirectoryEntry):
            super().__init__()
            self.entry = entry

    class Changed(Message):
        """Signal that this directory entry has changed."""

        bubble: ClassVar[bool] = False

    def __init__(
            self, dir_path: Path, init_patterns: str = '*',
            *, init_recurse: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.init_dir_path = Path(dir_path).resolve()
        self.init_patterns = init_patterns.split()
        self.init_recurse = init_recurse
        self.being_setup = not dir_path

    def compose(self) -> Iterator[Widget]:
        """Build the widget hierarchy."""
        with Horizontal():
            symbol = '✔' if self.being_setup else '-'
            yield CompactButton(label=symbol)
            yield Checkbox(value=self.init_recurse)
            with Vertical():
                path = Path(self.init_dir_path)
                rel_path = path.relative_to(self.app.root_path)
                yield PlainStatic(f'{rel_path}/', classes='dir_name')
                for pattern in self.init_patterns:
                    yield GlobEntry(pattern=pattern)
                yield MiniButton(label=' + ', classes='add_button')
        del (
            self.init_patterns, self.init_recurse, self.init_dir_path,
            self.being_setup)

    @on(Mount)
    async def handle_mount(self, _event: Mount) -> None:
        """Perform post-mount actions."""
        self.set_disabled_properties()

    @on(Button.Pressed)
    def accept_or_remove_entry(self, event: Button.Pressed) -> None:
        """Accept this entryor remove depending on current state."""
        button = event.button
        if str(button.label) == '-':
            self.post_message(self.RemovePressed(self))
        else:
            self.post_message(self.Accepted())

    def set_disabled_properties(self) -> None:
        """Set the disabled property of buttons to match configuration."""
        nodes = self.query(GlobEntry).nodes
        if nodes:
            nodes[0].set_button_disabled(flag=len(nodes) <= 1)

    def finish_setup(self):
        """Finish setting this as a new entry.

        Essentially, just change the button's 'tick' label to a 'minus' label.
        """
        self.query_one(CompactButton).label = '-'
        self.set_disabled_properties()

    @on(GlobEntry.RemovePressed)
    def remove_glob_entry(self, event: GlobEntry.RemovePressed):
        """Remove a `GlobEntry`."""
        event.entry.remove()
        self.set_disabled_properties()
        self.app.screen.set_focus(None)
        self.post_message(self.Changed())

    @on(MiniButton.Pressed, '.add_button')
    def add_glob_entry(self, _event: MiniButton.Pressed) -> None:
        """Add a new `GlobEntry`."""
        new_entry = GlobEntry(pattern='*')
        self.mount(new_entry, before='.add_button')
        self.set_disabled_properties()
        self.app.screen.set_focus(None)
        self.post_message(self.Changed())

    @on(GlobEntry.Changed)
    def forward_change(self, _event: GlobEntry.Changed) -> None:
        """Post a Changed message for this DirectoryEntry."""
        self.post_message(self.Changed())

    def rule(self) -> Rule:
        """Generate the watch rule for this directory.

        :return:
            A `Rule` named tuple of the directory `Path`, the recurse flag and
            a set of the glob patterns.
        """
        path_name = str(self.query_one('.dir_name').renderable)
        entries: list[GlobEntry] = self.query(GlobEntry).nodes
        recurse = self.query_one(Checkbox).value
        path = Path(path_name)
        if not path.is_absolute():
            path = self.app.root_path / path
        return Rule(
            path.resolve(), recurse,
            frozenset({entry.pattern_path for entry in entries}))


class DirectoryListView(Widget):
    """A list of directories."""

    DEFAULT_CSS = '''
        DirectoryListView {
            height: auto;
            width: auto;
            layout: vertical;
        }
        DirectoryListView DirectoryEntry {
            height: 2;
            width: 40;
            layout: vertical;
        }
        DirectoryListView .heading1 {
            width: auto;
            margin: 0 0 0 7;
            border-bottom: solid $primary-lighten-3;
        }
        DirectoryListView .heading2 {
            width: 1fr;
            margin: 0 2 0 2;
            border-bottom: solid $primary-lighten-3;
        }
        DirectoryListView > CompactButton {
            min-width: 3;
        }
    '''
    class UpdatedRules(Message):
        """Inidication of an updated file watching rule set."""

        def __init__(self, rules: set[Rule]):
            super().__init__()
            self.rules: set[Rule] = rules

    def __init__(self, config: configuration.Config, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        self.new_entry: DirectoryEntry | None = None
        self._rules: set[Rule] = set()

    @property
    def rules(self) -> set[RuleEntry]:
        """The current set of rules.

        :return:
            A set where each entry is a `Rule`:
        """
        return self._rules

    def compose(self) -> Iterator[Widget]:
        """Build the widget hierarchy."""
        with Horizontal():
            yield PlainStatic('Recurse', classes='heading1')
            yield PlainStatic(
                f'Paths (relative to: {self.app.root_path}) and file patterns',
                classes='heading2')
        run_section: configuration.Node = self.config.lookup_section('run')
        for n in range(20):
            path = run_section.lookup_item(f'watch_{n:02}-path').value
            recurse = run_section.lookup_item(f'watch_{n:02}-recurse').value
            patterns = run_section.lookup_item(f'watch_{n:02}-patterns').value
            if path:
                yield DirectoryEntry(
                    Path(path), init_patterns=patterns, init_recurse=recurse)
        yield CompactButton(label='+', classes='add_directory')
        yield (w := PureDirectoryTree(self.app.root_path, classes='dir_tree'))
        w.display = False

    @on(Mount)
    def post_initial_rules(self, event: Mount):
        """Post the initial rules."""
        self._handle_possible_rules_change()

    @on(Button.Pressed, '.add_directory')
    def start_adding_new_entry(self, _event: Button.Pressed):
        """Open the directory tree to allow a adding a new directory."""
        self.query_one(DirectoryTree).display = True
        self.query_one('.add_directory').display = False
        self.new_entry = DirectoryEntry('')
        self.mount(self.new_entry, before='.add_directory')

    @on(DirectoryTree.DirectorySelected)
    def select_directory(
            self, event: DirectoryListView.DirectorySelected) -> None:
        """Enter the selected directory into the new list slot."""
        relpath = event.path.relative_to(self.app.root_path)
        name = self.new_entry.query_one('.dir_name')
        name.update(str(relpath))

    @on(DirectoryEntry.Accepted)
    def finish_adding_directory(self, event: DirectoryEntry.Accepted) -> None:
        """Finish adding a new directory to the list."""
        self.query_one(DirectoryTree).display = False
        self.query_one('.add_directory').display = True
        self.new_entry.finish_setup()
        self.new_entry = None
        self._handle_possible_rules_change()

    @on(DirectoryEntry.RemovePressed)
    def remove_entry(self, event: DirectoryEntry.RemovePressed) -> None:
        """Remove a directory entry at the user's request."""
        event.entry.remove()
        self._handle_possible_rules_change()

    @on(DirectoryEntry.Changed)
    def check_for_rules_change(self, _event: DirectoryEntry.Changed) -> None:
        """Check for and handle change in the rules set."""
        self._handle_possible_rules_change()

    def _handle_possible_rules_change(self):
        """Generate the watch rules."""
        entries = self.query(DirectoryEntry).nodes
        rules: set[Rule] = {entry.rule() for entry in entries}
        if rules != self._rules:
            self._rules = rules
            self.post_message(self.UpdatedRules(rules))
        self._save_config()

    def _save_config(self):
        """Save the file monitoring set up to the project configuration."""
        run_section: configuration.Node = self.config.lookup_section('run')
        entries = self.query(DirectoryEntry).nodes
        with self.config.block_update():
            n =  -1
            for n, entry in enumerate(entries):
                rule = entry.rule()
                run_section.set(f'watch_{n:02}-path', str(rule.path))
                run_section.set(f'watch_{n:02}-recurse', rule.recurse)
                patterns = ' '.join(str(p) for p in sorted(rule.patterns))
                run_section.set(f'watch_{n:02}-patterns', patterns)
            for i in range(n + 1, 20):
                run_section.set(f'watch_{i:02}-path', '')
                run_section.set(f'watch_{i:02}-recurse', value=True)
                run_section.set(f'watch_{i:02}-patterns', '')


class FileWatchPanel(TabPane):
    """Panel for the file watching configuration."""

    DEFAULT_CSS = '''
        FileWatchPanel {
            layout: vertical;
        }
    '''

    dir_list_view: DirectoryListView

    def __init__(self, control: FileWatchController):
        super().__init__('File monitoring', id='file_monitor_pane')
        self.control = control
        self.watcher = FileWatcher(self)

    @property
    def config(self) -> configuration.Config:
        """The application configuration."""
        return self.control.config

    def compose(self) -> Iterator[Widget]:
        """Build up the widget tree for the result browser pane."""
        self.dir_list_view = DirectoryListView(self.config)
        yield self.dir_list_view

    @on(Mount)
    async def setup_watcher(self, _event: Mount) -> None:
        """Set up the file system watcher."""
        self.watcher.set_event_loop(asyncio.get_running_loop())

    @on(DirectoryListView.UpdatedRules)
    def apply_rules_change(
            self, event: DirectoryListView.UpdatedRules) -> None:
        """Reprogram the watcher with modified rules."""
        self.watcher.clear()
        rule: file_watching.Rule
        for rule in event.rules:
            self.watcher.add_directory(rule.path, recursive=rule.recurse)

    def handle_fs_change(
            self, fs_event: file_watching.FileSystemEvent) -> None:
        """Convert FileSystemEvent to WatchedFilesChanged message.

        This is invoked directly by the `FileWatcher`.
        """
        rule: file_watching.Rule
        path = Path(fs_event.src_path)
        changed_dirpath = path.parent
        #s = []
        #s.append(f'DEB: FileWatchPanel handle_fs_change {path}')
        #s.append(f'     {changed_dirpath=}')
        #for rule in self.dir_list_view.rules:
        #    s.append(f'        {rule}')
        # print('\n'.join(s))
        for rulepath, recurse, pattern_paths in self.dir_list_view.rules:
            if rulepath != changed_dirpath:
                continue
            for pattern_path in pattern_paths:
                if path.match(str(pattern_path)):
                    #print(f'DEB: FileWatchPanel post_message')
                    self.post_message(WatchedFilesChanged())
                    return


class FileWatchController(Controller):
    """Controller for the file watching configuration pane."""

    def __init__(self, app: tui_app.PytestApp):
        super().__init__(app)
        self.wibble: str = ''

    def compose(self) -> Iterator[Widget]:
        """Build up the widget tree for the result browser pane."""
        yield FileWatchPanel(control=self)
