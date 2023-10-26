"""Control functionality."""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.message_pump import MessagePump

if TYPE_CHECKING:
    from . import configuration
    from .app import PytestApp, Reporter


class Controller(MessagePump):
    """Base for the controller classes.

    All controllers are also textual MessagePumps.
    """

    def __init__(self, app: PytestApp):
        super().__init__(parent=app)
        # These are needed to make Controller classes work as message pimps
        # using the new (internal) textual.Signal mechanism.
        # TODO: So I think that this Controller class needs a much less
        #       fragile basis.
        self._pruning = False
        self.ancestors_with_self = []

    @property
    def app(self) -> PytestApp:
        """The parent application."""
        return self._parent

    @property
    def config(self) -> configuration.Config:
        """The loaded configuration files object."""
        return self.app.config

    def start(self):
        """Start this controller's messaage pump."""
        self._start_messages()

    def compose(self) -> list:                    # pylint: disable=no-self-use
        """Yield no widgets by default."""
        yield from ()
