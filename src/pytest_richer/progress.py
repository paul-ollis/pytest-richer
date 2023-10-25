"""Progress display support."""
from __future__ import annotations

import re
import time
import weakref
from functools import partial
from typing import TYPE_CHECKING

from rich.live import Live
from rich.markup import escape

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Callable

    import pytest
    from rich.console import Console

    from .helpers import Helper


def long_enough(max_time: float) -> Iterator[bool]:
    """Generate True each time 'max_time' has elapsed."""
    prev_trigger = time.time()
    while True:
        now = time.time()
        if now - prev_trigger >= max_time:
            yield True
            prev_trigger = now
        else:
            yield False


class TestProgressMapper:
    """Management mapping from test nodeid to progress display bars."""

    def __init__(self, helper: Helper, items: dict[str, pytest.Item]):
        self.item_to_group_name: dict[str, str] = {}
        self.item_map: dict[str, list[pytest.Item]] = {}
        self.is_cont: set[str] = set()
        self.label_map: dict[str, str] = {}

        # Try grouping tests by test script.
        item_map = self.item_map
        root = helper.config.rootpath
        for item in items.values():
            name = str(item.path.relative_to(root))
            if name not in item_map:
                item_map[name] = []
            item_map[name].append(item)
            self.item_to_group_name[item.nodeid] = name
        self._split_long_groups(helper.console)

        # If there are too many groups for the screen height, try grouping by
        # directory name.
        if len(item_map) > helper.console.size.height - 6:
            self.item_map.clear()
            self.label_map.clear()
            self.is_cont.clear()
            for item in items.values():
                name = str(item.path.relative_to(root).parent)
                if name not in item_map:
                    item_map[name] = []
                item_map[name].append(item)
                self.item_to_group_name[item.nodeid] = name
            self._split_long_groups(helper.console)

        def get_key(name):
            m = re.match(r'(.*)\[(\d+)\]$', name)
            if m:
                return m.group(1), int(m.group(2))
            else:
                return name, 0                               # pragma: no cover

        self.item_map = {n: item_map[n] for n in sorted(item_map, key=get_key)}

    def _split_long_groups(self, console: Console):
        """Split groups that will not fit on a single line."""
        item_map = self.item_map
        space = console.size.width - self.name_width - 9
        for name, items in list(item_map.items()):
            if len(items) > space:
                item_map.pop(name)
                n = 1
                old_items = list(items)
                while old_items:
                    sub_name = f'{name}[{n}]'
                    item_map[sub_name], old_items = (
                        old_items[:space], old_items[space:])
                    for item in item_map[sub_name]:
                        self.item_to_group_name[item.nodeid] = sub_name
                    if n == 1:
                        self.label_map[sub_name] = name
                    else:
                        self.label_map[sub_name] = ''
                    n += 1

    @property
    def name_width(self) -> int:
        """The width required to display group names."""
        return max((len(str(p)) for p in self.item_map), default=10)

    def group(self, nodeid: str) -> str:
        """Find the name of the group for a given test."""
        return self.item_to_group_name[nodeid]

    def items_for_test_group(self, nodeid: str) -> str:
        """Find test items in the group for a given test."""
        return self.item_map[self.group(nodeid)]


class ProgressBar:
    """Base for Paul's new, hopefully faster, progress bars."""

    parent: ProgressDisplay

    def __init__(self, *, label: str):
        self.label = label
        self._dirty = True
        self._cache = ''

    @property
    def label_width(self) -> int:
        """The width required for the label column."""
        return self.parent.label_width

    @property
    def perc_width(self) -> int:
        """The width required for percant complete column."""
        return self.parent.perc_width

    def render(self) -> str:
        """Render this bar as a line of text."""
        if self._dirty:
            perc = self.render_perc()
            label = f'{escape(self.label):<{self.label_width}}'
            self._cache = f'{perc}{label} {self.render_dynamic()}'
        return self._cache

    def render_perc(self) -> str:
        """Render the percent complete section of this bar."""
        return ' ' * self.perc_width

    def render_dynamic(self) -> str:              # pylint: disable=no-self-use
        """Render the dynamic section of this bar."""
        return ''


class PercentageProgressBar(ProgressBar):
    """Paul's new, hopefully faster, percantage progress bar."""

    def __init__(self, *, nitems: int, label: str):
        super().__init__(label=label)
        self.nitems = nitems
        self.count = 0

    def update(self, count: int) -> None:
        """Update this progress bar's count."""
        if self.count != count:
            self.count = count
            self._dirty = True

    def render_perc(self) -> str:
        """Render the percent complete section of this bar."""
        if self.count >= self.nitems:
            return '[cyan][100%][/cyan] '
        else:
            p = int(min(self.count / self.nitems * 100.0, 99.0))
            return f'[cyan][{p:<3}%][/cyan] '


class SlottedProgressBar(PercentageProgressBar):
    """Paul's new, hopefully faster, slotted progress bar."""

    def __init__(self, *, nitems: int, label: str):
        super().__init__(label=label, nitems=nitems)
        self.slots = ['.'] * nitems
        self.count = 0

    def update(self, count: int, *, idx: int, char_str: str) -> None:
        """Update this progress bar's count and progress slot."""
        # pylint: disable=arguments-differ
        super().update(count)
        if self.slots[idx] != char_str:
            self.slots[idx] = char_str
            self._dirty = True

    def render_dynamic(self) -> str:
        """Render the dynamic section of this bar."""
        return ''.join(self.slots)


class DescriptiveProgressBar(ProgressBar):
    """Paul's new, hopefully faster, descriptive progress bar."""

    def __init__(self, *, label: str):
        super().__init__(label=label)
        self.text: str = ''

    def update(self, *, text: str):
        """Update the dynamic text of this progress bar."""
        if self.text != text:
            self.text = text
            self._dirty = True

    def render_dynamic(self) -> str:
        """Render the dynamic section of this bar."""
        return self.text


class GraphBar(ProgressBar):
    """Paul's new, hopefully faster, descriptive graph bar."""

    def __init__(self, *, label: str, max_count: int):
        super().__init__(label=label)
        self.max_count = max_count
        self.count = 0

    def update(self, *, count: int):
        """Update this progress bar's count."""
        if self.count != count:
            self.count = count
            self._dirty = True

    def render_dynamic(self) -> str:
        """Render the dynamic section of this bar."""
        bar_fg = '=' * self.count
        bar = bar_fg.ljust(self.max_count, '-')
        return f'{self.count:<3} |{bar}|{self.max_count}'


class ProgressDisplay:
    """Paul's new, hopefully faster, progress display."""

    def __init__(self, console: Console):
        self.console = console
        self.bars: dict[str, ProgressBar] = {}
        self._label_width = -1
        self._perc_width = -1
        self.time_trigger = long_enough(0.1)
        self.live = Live(
            console=console,
            auto_refresh=False,
            get_renderable=self.get_renderable)
        self.stored_output: list[Callable[[], None]] = []

    def add_bar(self, name: str, *, bar: ProgressBar):
        """Add a bar to this progress display."""
        if name not in self.bars:
            self.bars[name] = bar
            bar.parent = weakref.proxy(self)
            self._label_width = -1
            self._perc_width = -1
            if not self.live.is_started:
                self.start()

    def start(self):
        """Start or resume the live display of this progress bar."""
        if not self.live.is_started:
            self.live.start()

    def stop(self):
        """Stop the live display of this progress bar."""
        if self.live.is_started:
            self.live.stop()
            for func in self.stored_output:
                func()
            self.stored_output[:] = []

    def get_renderable(self) -> str:
        """Get a renderable form of this progress display.

        This is called by the Rich.Live instance.
        """
        lines = [bar.render() for bar in self.bars.values()]
        return '\n'.join(lines)

    def update(self, name: str, *, refresh: bool = False, **kwargs) -> None:
        """Update a given progress bar.

        :name:    The name identifying the ProgressBar.
        :refresh: If set then the display will unconditionally refreshed.
        :kwargs:  The keyword argument required by the progress bar's update
                  method.
        """
        bar = self.bars.get(name)
        if bar:
            bar.update(**kwargs)
        triggered = next(self.time_trigger)
        if triggered or refresh:
            self.live.refresh()

    def handle_output(self, func, *args, **kwargs):
        """Handle a non-progress output request.

        If the live display is active then the request is buffered until live
        output ends.

        :func:   The function that will produuce the output.
        :args:   Positional arguments for the function.
        :kwargs: The keyword arguments for the function.
        """
        if self.live.is_started:
            self.stored_output.append(partial(func, *args, **kwargs))
        else:
            func(*args, **kwargs)

    @property
    def label_width(self):
        """The width required for the label column."""
        if self._label_width < 0:
            self._label_width = max(
                (len(bar.label) for bar in self.bars.values()), default=10)
        return self._label_width

    @property
    def perc_width(self):
        """The width required for percant complete column."""
        if self._perc_width < 0:
            for bar in self.bars.values():
                if isinstance(bar, PercentageProgressBar):
                    self._perc_width = 7
                    break
            else:
                self._perc_width = 0
        return self._perc_width
