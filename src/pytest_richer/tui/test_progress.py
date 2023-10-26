"""Progress display support."""
from __future__ import annotations

import functools
import re
import time
import weakref
from functools import partial
from typing import TYPE_CHECKING

from rich.markup import escape
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.geometry import Region, Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest

    from ..protocol import ItemRepresentation as PytestItem

import pytest_richer
from pytest_richer.tui import model
dlog = pytest_richer.get_log('main')


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
    """Management of mapping from test nodeid to progress display bars.

    This uses the nominal display area dimensions to work how to group tests
    in order to provide a practical progress display.

    Grouping by test script is preferred with large groups split over several
    lines in order to fit within the available width. If this grouping requires
    too many lines, tests are grouped by directory, once again splitting long
    groups over several lines as required.

    :size:
        A tuple of widgth, height defining the available size for the test
        progress bar layout.
    :rootpath:
        The root directory used to convert pytest script paths to relative
        form.
    :items:
        A mapping from nodeid to `PytestItem` instances.

    @item_to_group:
        A mapping from a test nodeid to the name of the group containing the
        test. Currently tests are group by script name or dirctory name.
    @item_map:
        A mapping from a group name to a list of `PytestItem` instances.
    """

    def __init__(
            self, size: tuple[int, int], root_path: Path,
            items: dict[str, PytestItem]):
        self.item_to_group_name: dict[str, str] = {}
        self.item_map: dict[str, list[pytest.Item]] = {}
        self.is_cont: set[str] = set()
        self.label_map: dict[str, str] = {}

        # Try grouping tests by test script.
        width, height = size
        item_map = self.item_map
        for item in items.values():
            name = str(item.path.relative_to(root_path))
            if name not in item_map:
                item_map[name] = []
            item_map[name].append(item)
            self.item_to_group_name[item.nodeid] = name
        self._split_long_groups(width)

        # If there are too many groups for the screen height, try grouping by
        # directory name.
        if len(item_map) > height - 6:
            self.item_map.clear()
            self.label_map.clear()
            self.is_cont.clear()
            for item in items.values():
                name = str(item.path.relative_to(root_path).parent)
                if name not in item_map:
                    item_map[name] = []
                item_map[name].append(item)
                self.item_to_group_name[item.nodeid] = name
            self._split_long_groups(width)

        def get_key(name):
            m = re.match(r'(.*)\[(\d+)\]$', name)
            if m:
                return m.group(1), int(m.group(2))
            else:
                return name, 0                               # pragma: no cover

        self.item_map = {n: item_map[n] for n in sorted(item_map, key=get_key)}

    def _split_long_groups(self, width: int):
        """Split groups that will not fit on a single line."""
        item_map = self.item_map
        space = max(30, width - self.name_width - 9)
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
        """Look up the name of the group for a given test."""
        return self.item_to_group_name[nodeid]

    def items_for_test_group(self, nodeid: str) -> list[PytestItem]:
        """Find test items in the group for a given test."""
        return self.item_map[self.group(nodeid)]


class ProgressBar:
    """Base a progress bar within a multi-line progress display."""

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

    def render_strip(self) -> Strip:
        """Render this bar as a Strip instance."""
        if self._dirty:
            perc = self.render_perc_segments()
            label = Segment(f'{escape(self.label):<{self.label_width}}')
            dyn_segments = self.render_dynamic_segments()
            self._cache = Strip([*perc, label, Segment(' '), *dyn_segments])
        return self._cache

    def render_perc_segments(self) -> list[Segment]:
        """Render the percent complete section of this bar."""
        return [Segment(' ' * self.perc_width)]

    def render_dynamic_segments(
            self) -> list[Segments]:              # pylint: disable=no-self-use
        """Render the dynamic section of this bar."""
        return [Segment('')]

    def render_width(self) -> int:
        """Calculate the width required to render this bar."""
        strip = self.render_strip()
        return strip.cell_length


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

    def render_perc_segments(self) -> list[Segment]:
        """Render the percent complete section of this bar."""
        style = Style.parse('cyan')
        if self.count >= self.nitems:
            text = '100%'
        else:
            p = int(min(self.count / self.nitems * 100.0, 99.0))
            text = f'{p:<3}%'
        return [Segment(text, style), Segment(' ')]


class SlottedProgressBar(PercentageProgressBar):
    """Paul's new, hopefully faster, slotted progress bar."""

    def __init__(self, *, nitems: int, label: str):
        super().__init__(label=label, nitems=nitems)
        self.text_slots = ['.'] * nitems
        self.slots = ['.'] * nitems
        self.count = 0

    def update(
            self, count: int, *, idx: int, result: model.NodeResult,
        ) -> None:
        """Update this progress bar's count and progress slot."""
        # pylint: disable=arguments-differ
        super().update(count)
        char_str = result.plain_indicator
        if self.text_slots[idx] != char_str:
            self.slots[idx] = result
            self._dirty = True

    def render_dynamic_segments(self) -> list[Segments]:
        """Render the dynamic section of this bar."""
        segments = []
        console = self.parent.app.console
        for s in self.slots:
            if isinstance(s, str):
                segments.append(Segment(s))
            else:
                segments.extend(s.render(console))
        return segments

    def render_dynamic_segments(self) -> list[Segments]:
        """Render the dynamic section of this bar."""
        def flush():
            if run:
                if isinstance(prev_result, str):
                    segments.append(Segment(''.join(run)))
                else:
                    rich_text = Text.from_markup(
                        prev_result.style_indicator_run(run))
                    segments.extend(rich_text.render(console))
                run[:] = []

        segments = []
        console = self.parent.app.console
        run = []
        prev_result = ''
        for result_or_text in self.slots:
            if result_or_text == '.':
                text = '.'
            else:
                text = result_or_text.plain_indicator
            if not run or run[-1] == text:
                run.append(text)
            else:
                flush()
                run.append(text)
            prev_result = result_or_text
        flush()
        return segments


class DescriptiveProgressBar(ProgressBar):
    """Paul's new, hopefully faster, descriptive progress bar."""

    def __init__(self, *, label: str):
        super().__init__(label=label)
        self.text: str = Text('')

    def update(self, *, text: str):
        """Update the dynamic text of this progress bar."""
        m_text = Text.from_markup(text)
        if self.text != m_text:
            self.text = m_text
            self._dirty = True

    def render_dynamic_segments(self) -> list[Segments]:
        """Render the dynamic section of this bar."""
        console = self.parent.app.console
        return list(self.text.render(console))


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

    def render_dynamic_segments(self) -> list[Segments]:
        """Render the dynamic section of this bar."""
        bar_fg = '=' * self.count
        bar = bar_fg.ljust(self.max_count, '-')
        return [Segment(f'{self.count:<3} |{bar}|{self.max_count}')]


class ProgressDisplay(ScrollView):
    """A multi-line progress display."""

    DEFAULT_CSS = """
    Static {
        height: auto;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bars: dict[str, tuple[ProgressBar, int]] = {}
        self._bars_by_index: list[tuple[ProgressBar, str]] = []
        self._label_width = -1
        self._perc_width = -1
        self._cached_size = (0, 0)
        self._size_update_timer: Timer | None = None

    def update_virtual_size(self):
        """Schedule an update or the virtual_size property."""
        if self._size_update_timer is None:
            self._size_update_timer = self.app.set_timer(
                0.1, self._update_virtual_size)

    def _update_virtual_size(self):
        self._size_update_timer = None
        width = max(self.max_render_width(), 79)
        height = len(self.bars)
        size = width, height
        if self._cached_size != size:
            self._cached_size = size
            self.virtual_size = Size(*size)

    def reset(self):
        """Reset display so that it has no content."""
        self.bars.clear()
        self._bars_by_index[:] = []
        self._label_width = -1
        self._perc_width = -1

    def add_bar(self, name: str, *, bar: ProgressBar):
        """Add a bar to this progress display."""
        if name not in self.bars:
            self._bars_by_index.append((bar, name))
            bar.parent = weakref.proxy(self)
            self._label_width = -1
            self._perc_width = -1
            self.update_virtual_size()
            self._number_bars()

    def remove_bar(self, name) -> None:
        """Remove a progress bar from the display."""
        if name in self.bars:
            bar, index = self.bars.pop(name)
            self._bars_by_index.remove((bar, name))
            self._number_bars()

    def _number_bars(self) -> None:
        """Number each progress bar."""
        for index, (bar, name) in enumerate(self._bars_by_index):
            self.bars[name] = bar, index

    # TODO: The refresh argument is dead.
    def update_bar(
            self, name: str, *, refresh: bool = False, **kwargs) -> None:
        """Update a given progress bar.

        :name:    The name identifying the ProgressBar.
        :refresh: If set then the display will be unconditionally refreshed.
        :kwargs:  The keyword argument required by the progress bar's update
                  method.
        """
        bar, index = self.bars.get(name)
        if bar:
            bar.update(**kwargs)
            self.update_virtual_size()
            full_width = self.size.width
            region = Region(x=0, y=index, width=full_width, height=1)
            self.refresh(region)

    def lookup_bar(self, name: str) -> ProgressBar | None:
        """Lookup a bar with a given name."""
        return self.bars.get(name)

    def render_line(self, y: int) -> Strip:
        """Render a single line for the progress display.

        This is invoked by the Textual machinery as required.
        """
        x_off, y_off = self.scroll_offset
        try:
            bar, _ = self._bars_by_index[y + y_off]
        except IndexError:
            return Strip('')
        strip = bar.render_strip()
        ret = strip.crop(x_off, x_off + self.size.width)
        return ret

    def redraw(self):
        """Make the progress display refresh on screen."""
        self.refresh(layout=True)

    def max_render_width(self) -> int:
        """Calculate the maximum cell width required for rendering."""
        try:
            return max(bar.render_width() for bar, _ in self._bars_by_index)
        except ValueError:
            # TODO: Figure out why this can happen. This is really just masking
            #       a bug. Seen when nearly all tests failed with setup errors.
            return 79

    @property
    def avail_size(self) -> tuple[int, int]:
        """The available dimensions as a width, height tuple.

        This is basically the size of the parent container. The value is not
        constant since it depends on the terminal size and layout.
        """
        r = self.parent.region
        return r.width, r.height

    @property
    def label_width(self):
        """The width required for the label column."""
        if self._label_width < 0:
            self._label_width = max(
                (len(bar.label) for bar, _ in self.bars.values()), default=10)
        return self._label_width

    @property
    def perc_width(self):
        """The width required for percant complete column."""
        if self._perc_width < 0:
            for bar, _ in self.bars.values():
                if isinstance(bar, PercentageProgressBar):
                    self._perc_width = 5
                    break
            else:
                self._perc_width = 0
        return self._perc_width


@functools.cache
def indicator_as_rich_text(ind_str: str) -> Text:
    return Text.from_markup(ind_str)
