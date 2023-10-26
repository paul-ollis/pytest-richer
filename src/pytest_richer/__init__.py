"""A Rich based front-end for pytest."""
from __future__ import annotations

import asyncio
import atexit
import os
import queue
import threading
import time
import traceback
from typing import Callable, ClassVar

__all__ = [
    'arun_and_log_error',
    'get_log',
    'Logger',
    'run_and_log_error',
]

main_thread_ident = threading.main_thread().ident


class LoggerFile:
    """File based back end for a `Logger`."""

    _files: ClassVar[dict[str: LoggerFile]] = {}

    def __init__(self, path_str: str | None = None):
        worker = os.environ.get('PYTEST_XDIST_WORKER')
        self.f = None
        self.path_str = path_str
        if path_str is not None:
            self.f = open(
                f'{path_str}.log', 'wt', newline=None, buffering=1,
                encoding='utf-8', errors='replace')
        else:
            self.f = None
        self._files[path_str] = self

    @classmethod
    def get(cls, path_str: str | None) -> Logger:
        """Create or get a LoggerFile for a given file name."""
        if path_str not in cls._files:
            cls._files[path_str] = LoggerFile(path_str)
        return cls._files[path_str]

    def write(self, s: str):
        lines = s.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            t = time.time()
            for line in lines:
                self.f.write(f'{t:.6f}: {line}\n')

    def flush(self):
        if self.f:
            self.f.flush()

    def close(self) -> None:
        if self.f is not None:
            self.f.close()
            self.f = None


class ThreadLoggerFile:
    """`Logger` backend that writes to a file via a pipe."""

    _files: ClassVar[dict[str: LoggerFile]] = {}

    def __init__(self, path_str: str):
        self.queue = queue.Queue()
        self.path_str = path_str
        self.f = open(
            f'{path_str}.log', 'wt', newline=None, buffering=1,
            encoding='utf-8', errors='replace')
        self._files[path_str] = self

        self.thread = threading.Thread(target=self.run, name='log-writer')
        self._write_file('PAUL START MAIN')
        self.flush()
        self.thread.start()

    def run(self) -> None:
        self._write_file('START THREAD')
        self.flush()
        while True:
            s = self.queue.get()
            if s is None:
                break
            self._write_file(s)
        self._write_file(f'LOG thread for {self.path_str} done')
        self.f.close()

    def _write_file(self, s: str):
        self.f.write(f'{time.time():.6f}: {s}\n')

    def write(self, s: str):
        lines = s.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        ident = threading.current_thread().name
        for line in lines:
            self.queue.put(f'[{ident}] {line}')

    def flush(self):
        if self.f:
            self.f.flush()

    def close(self) -> None:
        if self.thread:
            self.queue.put(None)
            self.thread.join()
            self.thread = None


class Logger:
    """A logger that can be used in place of an ``io.TextIOBase``."""

    context: ClassVar[str] = ''
    _clones: ClassVar[dict[str: Logger]] = {}
    _main_log: ClassVar[Logger] = None

    def __init__(
            self,
            name: str,
            path_str: str = None,
            start: bool = True,
            threaded: bool = False,
        ):
        key = f'{name}-{self.context}'
        self._clones[key] = self
        if len(self._clones) == 1:
            self.__class__._main_log = self

        self.name = name
        if path_str:
            self.path_str = path_str
        elif self.context:
            self.path_str = f'{name}-{self.context}'
        else:
            self.path_str = name
        self.threaded = threaded
        self.logger_file: LoggerFile | ThreadLoggerFile | None = None
        if start:
            self.start()

    def start(self) -> None:
        if self.logger_file:
            return

        if self.threaded:
            thread_ident = threading.current_thread().ident
            if thread_ident == main_thread_ident:
                self.logger_file = ThreadLoggerFile(self.path_str)
        else:
            self.logger_file = LoggerFile.get(self.path_str)

    def stop(self) -> None:
        if self.logger_file:
            self.logger_file.close()
            self.logger_file = None

    @classmethod
    def cleanup(cls):
        """Clean up at exit time."""
        for logger in cls._clones.values():
            logger.stop()

    @classmethod
    def new(
            cls, name: str,
            path_str: str | None,
        ) -> Logger:
        """Create new logger with a different LoggerFile back end."""
        return Logger(name, path_str)

    @classmethod
    def get(cls, name: str) -> Logger:
        """Get a logger with the given name.

        If necessary this creates a clone of the main logger."""
        key = f'{name}-{cls.context}'
        if key not in cls._clones:
            return cls(name)
        else:
            return cls._clones[key]

    @property
    def active(self) -> bool:
        return self.logger_file and log_control.get(self.name, False)

    def set_file(self, path_str: str) -> None:
        """Set the file being logged to."""
        self.logger_file = LoggerFile.get(path_str)

    def write(self, s: str):
        if self.active:
            self.logger_file.write(s)

    def flush(self):
        if self.active:
            self.logger_file.flush()

    @classmethod
    def lookup_for_process(cls, lookup_name: str) -> Logger:
        key = lookup_name, os.getpid()
        return cls._per_proc_loggers[key]


def get_log(name: str) -> Logger:
    """Get a logger with the given name.

    If necessary this creates a clone of the main logger."""
    return Logger.get(name)


def run_and_log_error(log, func: Callable, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        print(
            f'Execution failed: {func}({args}, {kwargs})', file=log)
        traceback.print_exception(e, file=log)


async def arun_and_log_error(log, func: Callable, *args, **kwargs):
    try:
        return await func(*args, **kwargs)
    except Exception as e:
        print(
            f'Task failed: {func}({args}, {kwargs})', file=log)
        traceback.print_exception(e, file=log)


# Create a fallback logger.
fall_back_log = Logger(name='fallback', path_str='fallback.log', start=False)

log_control = {
    'main': True,                # The main, general log output.
    'main2': True,               # The main, general log output.
    'errors': True,              # General errors.
    'postmortem': False,         # Postmortem control and panel.
    'collect-errors': False,     # General errors.
    'protocol': True,            # Low level protocol data.
    'plugin': True,              # Logging for the pipe plugin.
}

atexit.register(Logger.cleanup)
