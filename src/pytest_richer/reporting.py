"""Support for handling pytest reports and related types."""
from __future__ import annotations

from contextlib import contextmanager
from typing import ClassVar, TYPE_CHECKING

from rich.ansi import AnsiDecoder
from rich.console import Group
from rich.syntax import Syntax

if TYPE_CHECKING:
    from collections.abc import Sequence
    from rich.console import Console


class TerminalFormatter:
    """An object formatter replacing the pytest.TerminalWriter.

    Many objects involved in pytest reports and reported output are designed to
    format their own output using a TerminalWriter, which is typically provided
    by the standar pytest.TerminalReporter.

    This provides a partial emulation of a TerminalWriter's, allowing the
    toterminal method of objects to be used to format output to that is is
    usable with a Rich.console.
    """

    ansi_decoder = AnsiDecoder()
    markup_map: ClassVar[dict[str, str]] = {
        'black': '[black]',
        'red': '[red]',
        'green': '[green]',
        'yellow': '[yellow]',
        'blue': '[blue]',
        'purple': '[purple]',
        'cyan': '[cyan]',
        'white': '[white]',
        'Black': '[bright_black]',
        'Red': '[bright_red]',
        'Green': '[bright_green]',
        'Yellow': '[bright_yellow]',
        'Blue': '[bright_blue]',
        'Purple': '[bright_purple]',
        'Cyan': '[bright_cyan]',
        'White': '[bright_white]',
        'bold': '[bold]',
        'light': '[dim]',
        'blink': '',                         # No. Just no!
        'invert': '[reverse]',
    }

    def __init__(self, console: Console, *, highlight_code: bool):
        self.console = console
        self.highlight_code = highlight_code
        self.buffer: list[str | Syntax] or None = None

    # Methods provides to suppoer pytest_richer.
    @contextmanager
    def capture(self) -> list[str | Syntax]:
        """Provide a context where output is captured rather than written.

        While the context is active to get the `buffer` attribute contains a
        list of renderables.
        """
        self.buffer = []
        yield self.buffer
        self.buffer = None

    def convert_markup(self, markup: dict[str, bool]) -> str:
        """Convert pytest markup flags to Rich style names."""
        return ''.join(
            self.markup_map.get(name, '')
            for name, flag in markup.items() if flag)

    def convert_ansi_codes(self, text: str) -> Group:
        """Convert ASNSI codes to Rich markup."""
        return Group(*self.ansi_decoder.decode(text))

    #
    # Methods use by pytest ``toterminal`` implementations.
    #
    @property
    def fullwidth(self) -> int:
        """The width of the console."""
        return self.console.size.width

    @property
    def width_of_current_line(self) -> int:
        """Return an estimate of the width so far in the current line."""
        msg = 'Cannot calculate space left on the current line.'
        raise RuntimeError(msg)

    def markup(self, text: str, **markup: bool) -> str:
        """Format text with defined markup."""
        start = self.convert_markup(markup)
        return f'{start}{text}[/]' if start else text

    def sep(
            self, sepchar: str, title: str = '', fullwidth: int = 0,
            **markup: bool,
        ) -> None:
        """Draw a separator line accross the width of the terminal.

        :sepchar:   Nominally a separator character, but it may be more than
                    one character.
        :title:     A title to embed within the separator line.
        :fullwidth: A value to over-ride the terminal width.
        :markup:    Markup flag values.
        """
        fullwidth = self.fullwidth if fullwidth <= 0 else fullwidth
        if title:
            # We are assuming sepchar is a single character here.
            text = f' {title} '.center(fullwidth, sepchar)
        else:
            text = (sepchar * fullwidth)[:fullwidth]
        self.line(text, **markup)

    def write(self, msg: str, *, flush: bool = False, **markup: bool) -> None:
        """Write a string with defined markup."""
        if msg:
            start = self.convert_markup(markup)
            text = f'{start}{msg}[/]' if start else msg
            if self.buffer is not None:
                self.buffer.append(text)
            else:
                self.console.print(text, end='')

    def line(self, s: str = '', **markup: bool) -> None:
        """Write text plus newline, with defined markup."""
        self.write(s, **markup)
        self.write('\n')

    def flush(self) -> None:
        """Flush pending output - a nul opearation for this class."""
