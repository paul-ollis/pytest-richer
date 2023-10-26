"""A controller for the dagnostics pane."""
from __future__ import annotations

import asyncio
import time
from functools import partial
from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from rich.traceback import Traceback
from textual.color import Color
from textual.containers import ScrollableContainer
from textual.widgets import Static, TabPane

from .control import Controller

if TYPE_CHECKING:
    from collections.abc import Iterator

    from textual.widget import Widget

    from . import app as tui_app, configuration

PlainStatic = partial(Static, markup=False)


class Item:
    """Base for a single item displayed in the diagnostics panel."""

    def __init__(self):
        self.timestamp: float = time.time()

    def render(self) -> Static:
        """Render this item as a widget."""
        return PlainStatic(f'=== Item[{self.timestamp}] ===')

    def decorate(self, w: Widget) -> Widget:
        """Decorate a rendered widget."""
        lkup = w.app.get_css_variables()
        w.styles.border_top = ('solid', Color.parse(lkup['primary']))
        w.styles.padding = 0, 1, 0, 1
        w.border_title = time.strftime(
            '%Y/%m/%d %H:%M:%S', time.localtime(self.timestamp))
        return w


class MessageItem(Item):
    """A simple message."""

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def render(self) -> Static:
        """Render this item as a widget."""
        return self.decorate(PlainStatic(self.message))


class FailedTaskItem(Item):
    """Information about a failed task."""

    def __init__(self, task: asyncio.Task, exc: BaseException):
        super().__init__()
        self.task_str = str(task)
        self.tb = Traceback.from_exception(type(exc), exc, exc.__traceback__)

    def render(self) -> Static:
        """Render this item as a widget."""
        header = Text.assemble(('Task failure:', 'red'), f' {self.task_str}\n')
        return self.decorate(PlainStatic(Group(header, self.tb)))


class DiagnoticsController(Controller):
    """Controller for the internal diagnostics pane."""

    def __init__(self, app: tui_app.PytestApp):
        super().__init__(app)
        self.tasks: list[asyncio.Task] = []
        self.monitor: asyncio.Task | None = None
        self.canvas = ScrollableContainer(id='diag_dump')
        self.items: list[Item] = []

    def compose(self) -> Iterator[Widget]:
        """Build up the widget tree for the result browser pane."""
        with TabPane('Diagnostics', id='diag_pane'):
            yield self.canvas

    def monitor_task(self, task: asyncio.Task) -> None:
        """Add task to the set being monitored."""
        self.tasks.append(task)
        if self.monitor is None:
            self.monitor = asyncio.create_task(self.check_tasks())

    async def check_tasks(self):
        """Perform regular checks on the health of monitored tasks."""
        buf = []
        while True:
            await asyncio.sleep(0.2)
            done: list[asyncio.Task] = []
            s = []
            for task in self.tasks:
                if task.done():
                    done.append(task)
                    exc = task.exception()
                    if exc:
                        self.app.write_line(f'Task X {task} failed')
                        self.log_exception(task, exc)
                        import traceback
                        traceback.print_exception(exc)

            self.tasks[:] = [t for t in self.tasks if t not in done]
            if s:
                buf.extend(s)
                w = self.app.query_one('#diag_dump')
                w.update(''.join(buf))

    def log_message(self, message: str) -> None:
        """Log exception and traceback to the panel."""
        self.add_item(MessageItem(message))

    def log_exception(self, task: asyncio.Task, exc: BaseException) -> None:
        """Log exception and traceback to the panel."""
        self.add_item(FailedTaskItem(task, exc), show=True)

    def add_item(self, item: Item, *, show: bool = False) -> None:
        """Add and display a failed task item."""
        self.items.append(item)
        self.canvas.mount(item.render())

        if show:
            # Make this diagnostics tab visible.
            query = self.app.query('#diag_pane')
            pane = query.filter('TabPane').first()
            pane.parent.parent.show_tab('diag_pane')
