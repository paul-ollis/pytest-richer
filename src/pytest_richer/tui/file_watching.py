"""Support for watching changes to file.

This uses the watchdog library to do all the heavy lifting.

Note that other modules that use this library are responsible for checking that
the watchdog library is installed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from weakref import proxy

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    from pathlib import Path

    from watchdog.observers.api import ObservedWatch


class ThreadEventHandler(FileSystemEventHandler):
    """Our handler for events."""

    def __init__(self, handler: FileWatcher):
        self.handler = proxy(handler)
        self.loop = None

    def on_modified(self, event: FileSystemEvent):
        """Forward to FileWatcher.on_modified in the main thread.

        Only non-directory changes are forwarded.
        """
        if self.loop and not event.is_directory:
            self.loop.call_soon_threadsafe(self.handler.on_modified, event)


class FileWatcher(FileSystemEventHandler):
    """Simple wrapper around the ``watchdog`` package."""

    def __init__(self):
        self.watches: list[ObservedWatch] = []
        self.handler = ThreadEventHandler(self)
        self.observer = Observer()
        self.observer.start()

    def set_event_loop(self, loop):
        """Set the asyncio event loop for this watcher."""
        self.handler.loop = loop

    def add_directory(self, dir_path: Path, *, recursive: bool) -> None:
        """Add a directory to the set being watched."""
        watch = self.observer.schedule(
            self.handler, str(dir_path.resolve()), recursive=recursive)
        self.watches.append(watch)

    def clear(self) -> None:
        """Clear all current watches."""
        for watch in self.watches:
            self.observer.unschedule(watch)
        self.watches[:] = []

    def on_modified(self, event: FileSystemEvent):
        """Handle a file modification event."""
