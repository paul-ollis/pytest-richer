"""Globally defined application events."""

import copy

from textual.message import Message


class ControlMessage(Message, bubble=False):
    """Base for all messages that should be routed to all controllers."""

    logged = False

    def copy(self):
        """Create a shallow copy of this message."""
        return copy.copy(self)


class WatchedFilesChanged(ControlMessage):
    """Signal that one or more watched files have changed."""


class HIDEvent(Message):
    """Provider of detailed mouse event information.

    This is used to debug and demo purposes only.
    """

    def __init__(self, name: str):
        super().__init__()
        self.name = name

    def description(self) -> str:
        """Form description of this event."""
        return self.name


class TestSelectEvent(Message):
    """Indication that a test has been selected or deselected.

    :nodeid:   The pytest nodeid.
    :selected: True if selected and false if deselected.
    """

    def __init__(self, nodeid: str, *, selected: bool):
        super().__init__()
        self.nodeid = nodeid
        self.selected = selected


class TestFailure(ControlMessage):
    """Notification of a test failure."""

    def __init__(self, nodeid: str):
        super().__init__()
        self.nodeid = nodeid


class CollectionFailure(ControlMessage):
    """Notification of a test collection failure."""

    def __init__(self, nodeid: str):
        super().__init__()
        self.nodeid = nodeid


class EndTestRunMessage(ControlMessage):
    """Notification that a test run has completed."""
