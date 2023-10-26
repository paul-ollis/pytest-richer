"""Types that store basic test information."""
from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text

if TYPE_CHECKING:
    import warnings
    from typing import Literal

    from rich.console import Console as RichConsole, ConsoleOptions


class TestID(str):
    """A unique identifer for a test.

    The pytest framework calls this a ``nodeid``. We convert them to instances
    of TestID which provides methods to manipulate and interpret the
    componenents.

    This is a subclass of the built-in ``str`` and so can be used where a plain
    ``nodeid`` string is required.
    """

    __slots__ = ['_rootpath']

    def __new__(cls, value: str, *, rootpath: Path):
        """Create a new instance."""
        inst = str.__new__(cls,  value)
        inst._rootpath = rootpath                                # noqa: SLF001
        return inst

    @property
    def components(self) -> tuple[tuple[str, ...], tuple[str, ...], str]:
        """A breakdown into component of the test ID.

        This is a three part tuple:

        0: A tuple of file name components, relative to the test root.
        1: A tuple of the components of the test method/function canonical
           name, excluding the final part. This may be empty.
        2: The final part of the test ID as a plain string.
        """
        path_name, _, cname = self.partition('::')
        path = Path(path_name)
        with suppress(ValueError):
            path = path.relative_to(self._rootpath)
        cparts = tuple(cname.split('::'))
        return path.parts, cparts[:-1], cparts[-1]

    @property
    def parts(self) -> tuple[str]:
        """A simple breakdown of the test ID into parts.

        This is a tuple formaed by concatenation of the `componenents`.
        """
        a, b, c = self.components
        return a + b + (c,)


@dataclass
class PytestWarning:
    """A warning reported by pytest code."""

    warning_message: warnings.WarningMessage
    when: Literal['config', 'collect', 'runtest']
    nodeid: str
    filename: str | None
    line_number: int | None
    function: str | None

    def to_text(self) -> Text:
        """Render as a rich.Text."""
        def add_value(name, value):
            if value:
                prefix = f'{name}:'
                s.append(f'    {prefix:<12} {value}')

        s = ['Warning:']
        cat = self.warning_message.category
        add_value('message', self.warning_message.message)
        if cat:
            add_value('category', cat.__name__)
        add_value('file', self.warning_message.file)
        add_value('filename', self.warning_message.filename or self.filename)
        add_value('line', self.warning_message.line)
        add_value('line_number',
                  self.warning_message.lineno or self.line_number)
        add_value('source', self.warning_message.source)
        add_value('nodeid', self.nodeid)
        add_value('function', self.function)
        return Text.assemble(*[f'{el}\n' for el in s])

    def __rich_console__(
            self, console: RichConsole, options: ConsoleOptions,
        ) -> Text:
        """Render as a rich.Text."""
        return self.to_text()
