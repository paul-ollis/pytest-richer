"""An alternative TerminalReporter providing structured piped output.

This is used to run pytest as a sub-process with the parent process being
responsible for displaying progress and other output. The output produced by
this plugin is designed to be easy to interpret programatically, but is also
textual (mostly hex).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import queue
import sys
import threading
from typing import Literal, TYPE_CHECKING
from weakref import proxy

import pytest

import pytest_richer
from pytest_richer.protocol import encode

if TYPE_CHECKING:
    import warnings
    from collections.abc import Iterable, Sequence

    from _pytest.reports import ExceptionRepr
    from _pytest.terminal import TerminalReporter

dlog = pytest_richer.get_log('plugin')
HAVE_XDIST = importlib.util.find_spec('xdist') is not None

# The exit code for which summaries should be displayed.
summary_exit_codes = {
    pytest.ExitCode.OK,
    pytest.ExitCode.TESTS_FAILED,
    pytest.ExitCode.INTERRUPTED,
    pytest.ExitCode.USAGE_ERROR,
    pytest.ExitCode.NO_TESTS_COLLECTED,
}


def am_a_worker() -> bool:
    """Check that this is not a pytest-xdist worker process."""
    return os.environ.get('PYTEST_XDIST_WORKER') is not None


def in_main_thread():
    """Test if this is the main (driver) thread."""
    return threading.main_thread().ident == threading.current_thread().ident


# TODO: This is not required. the 'plugin.py' prevents code in here running
#       within worker processes.
def main_process_only(func):
    """Decorator to allow execution only in the main pytest_xdist process."""
    @contextlib.wraps(func)
    def invoke(*args, **kwargs):
        if not am_a_worker():
            return func(*args, **kwargs)
        else:
            print(f'Paul: Non-main invoke {func}', file=dlog)
            assert False, 'Should not happen!'
            return None

    return invoke


def clean_nodeid(reported_id: str) -> str:
    """Clean up a reported nodeid.

    When running under pytest-xdist with 'grouped' tests, the reported ID
    may have an, unwanted, appended '@<group-name>'. This is a pain because the
    '@' symbol may also appear in 'parametrized' un-modified IDs. Hopefully
    the logic here will cope with all cases.
    """
    nodeid, _, group = reported_id.rpartition('@')
    if nodeid:
        if group and group[-1] == ']':
            # Assume that the '@' is a result of 'parametrization'.
            return reported_id
        else:
            return nodeid
    else:
        return reported_id


class PipeRedirector:
    """File like object to redirect sys.stdout and sys.stderr to the pipe."""

    def __init__(self, reporter: RichPipeReporter, name: str):
        self.reporter = proxy(reporter)
        self.name = name

    @property
    def encoding(self):
        """The name of the encoding used to decode/decode the stream."""
        return self.reporter.pipe.encoding

    @property
    def error(self):
        """The error setting of the decoder or encoder."""
        return self.reporter.pipe.errors

    @property
    def newlines(self) -> str | tuple[str, ...] | None:
        """An indication or the newlines translated so far."""

    @staticmethod
    def detach():
        """Raise io.UnsupportedOperation."""
        raise io.UnsupportedOperation

    @staticmethod
    def read(_size=- 1, /):
        """Raise io.UnsupportedOperation."""
        raise io.UnsupportedOperation

    @staticmethod
    def readline(_size=- 1, /):
        """Raise io.UnsupportedOperation."""
        raise io.UnsupportedOperation

    @staticmethod
    def seek(_offset: int, _whence: int = 0, /):
        """Raise io.UnsupportedOperation."""
        raise io.UnsupportedOperation

    @staticmethod
    def tell():
        """Raise io.UnsupportedOperation."""
        raise io.UnsupportedOperation

    @staticmethod
    def fileno():
        """Raise OSError."""
        raise OSError

    @staticmethod
    def seekable():
        """Return ``False``."""
        return False

    @staticmethod
    def readable():
        """Return ``False``."""
        return False

    @staticmethod
    def writeable():
        """Return ``True``."""
        return True

    def write(self, s: str, /) -> int:
        """Write the string s to the stream.

        :return: The number of characters written.
        """
        self.reporter.put(self.name, s)
        return len(s)

    def writelines(self, lines: Iterable[str], /):
        """Write a sequence of lines."""
        for line in lines:
            self.write(line)

    @staticmethod
    def flush():
        """Do nothing. Output is unbufferred."""

    @staticmethod
    def isatty() -> bool:
        """Return ``True``.

        The pytest-richher application fron-end interprests terminal escape
        sequences.
        """
        return True


class RichPipeReporter:
    """A replacement for the standard pytest terminal reporter.

    This needs to provide quite a large number of hook methods. Most of them
    simply hand off to `Helper` based objects.
    """

    # pylint: disable=too-many-instance-attributes
    # pylint: disable=too-many-public-methods
    Status = Literal['collected', 'running', 'success', 'fail', 'error']

    def __init__(self, config: pytest.Config, std_reporter: TerminalReporter):
        self.pipe = sys.stdout
        self.stdout = PipeRedirector(self, 'copy_stdout')
        self.stderr = PipeRedirector(self, 'copy_stderr')
        self.std_reporter = std_reporter
        self.config = config
        self.pytest_session = None
        self.collection_thread: threading.Thread | None = None
        self.collection_thread_awaitable = False
        self.collection_active = False
        self.main_thread = threading.get_ident()
        self.pipe_writer_thread = self.main_thread
        self.monkey_patch_terminal_reporter()
        self.seen_warnings = set()
        self.run_phase_started = False
        self.put_queue = queue.Queue()
        self.put_thread = threading.Thread(
            target=self.put_from_queue, name='putter')
        self.put_thread.start()
        if not os.environ.get('RICHER_DEBUG', ''):
            self._setup_stdouts()
        self.put('proto_init', config)

        # This is required to support some standard pytest features. Currently
        # I know that the --duration option makes use of this.
        self.stats: dict[str, list] = {}

        self.started_count = 0

    def monkey_patch_terminal_reporter(self):
        """Patch parts of the terminal reporter.

        As far as I can tell, it is basically impossible to completely override
        that standard pytest terminal reporter in a completely clean way.

        This is my practical solution.
        """
        self.std_reporter.write = self.write
        self.std_reporter.rewrite = self.rewrite
        self.std_reporter.write_line = self.write_line

    def in_pipe_thread(self) -> bool:
        """Test if the current thread is the pipe writing thread."""
        return self.pipe_writer_thread == threading.get_ident()

    #
    # Overall test session management.
    #
    @pytest.hookimpl(tryfirst=True)
    @main_process_only
    def pytest_runtestloop(self):
        """Prepare for the start of test execution."""
        self.put('proto_runtestloop')
        print('Run test loop', file=dlog)

    @pytest.hookimpl(trylast=True)
    @main_process_only
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        """Perform required actions at the start of the session."""
        self.pytest_session = session
        self.put('proto_session_start', session)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_sessionfinish(
            self, session: pytest.Session, exitstatus: int | pytest.ExitCode,
        ) -> None:
        """Perform required actions at the end of the session."""
        outcome = yield
        # TODO: Need to trap and forward exceptions.
        print(f'Session end {exitstatus=}', file=dlog)
        outcome.get_result()  # For exception raising side effects.
        self.put('proto_session_end', exitstatus)
        if exitstatus in summary_exit_codes:
            self.config.hook.pytest_terminal_summary(
                terminalreporter=self, exitstatus=exitstatus,
                config=self.config)
        self._cleanup_colllection_thread()

    @main_process_only
    def pytest_unconfigure(self) -> None:
        """Perform final action before exiting."""
        print('Unconfigure', file=dlog)
        self.put('proto_pytest_unconfigure')
        self._cleanup_colllection_thread()
        self._cleanup_putter_thread()
        pytest_richer.Logger.cleanup()
        print('Unconfigure...clean up complete', file=dlog)

    #
    # Test collection processing.
    #
    @pytest.hookimpl(tryfirst=True)
    @main_process_only
    def pytest_collection(self) -> None:
        """Prepare for test collection.

        This is the hook used to perform test collection, but useable as an
        indication that test collection is about to start.
        """
        self.start_collection_thread_if_required()
        self.put('proto_test_collection_start')
        self.collection_active = True

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        """Forward a report about collected tests.

        The report can indicate collection failure.
        """
        # Running under pytest-xdist produces duplicate collection reports.
        # However the duplicates do not have a pickled_rich_traceback
        # attribute, a fact we use to identify and drop them.
        try:
            getattr(report, 'pickled_rich_traceback')
        except AttributeError:
            print('Drop duplicate collect report', file=dlog)
            return

        if not self.collection_active:
            print("ERROR? - Valid report, but not collecting!")
            return

        #print(
        #    f'Collect report: {report.outcome} {report.nodeid}', file=dlog)
        #if report.result:
        #    print('Pipe: ...results:', file=dlog)
        #    for obj in report.result:
        #        print(f'    {obj.__class__.__name__} {obj.name}', file=dlog)
        #if report.sections:
        #    print('Pipe: ...sections:', file=dlog)
        #    for obj in report.sections:
        #        print(f'    {obj}', file=dlog)
        self.put('proto_test_collect_report', report)

    def pytest_deselected(self, items: Sequence[pytest.Item]) -> None:
        """Forward a sequence of deselected test items."""
        self.put('proto_deselect_tests', items)

    @main_process_only
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Handle the completion of test collection.

        When running with pytest-xdist, this is expected to occur in the
        'par-collect' background thread. Otherwise this occurs in the main
        thread.
        """
        if self.in_pipe_thread():
            print('Collection finished: collection thread', file=dlog)
            self.put('proto_test_collection_finish')
            self.collection_active = False
            if self.collection_thread is not None:
                self.collection_thread_awaitable = True
                self.pipe_writer_thread = self.main_thread
        else:
            print('Collection finished: worker thread', file=dlog)

    def start_collection_thread_if_required(self):
        """Start a separate test collection thread if required."""
        numprocesses = self.config.getoption('numprocesses', None)
        if numprocesses is not None:
            # Looks like we are running under pytest-xdist. Do an extra
            # parallel collecion so that we have full test details. The
            # pytest-xdist plugin 'optimizes' away collection information so
            # this is the only way to provide collection stats.
            #
            # The separate thread allows the pytest-xdist driver to continue
            # managing its child processes.
            def do_collect():
                self.pipe_writer_thread = threading.get_ident()
                pts = self.pytest_session
                pts.items = pts.perform_collect()
                print('COLLECT thread done', file=dlog)

            print('Start collection thread', file=dlog)
            self.collection_thread = threading.Thread(
                target=do_collect, name='par-collect')
            self.collection_thread.start()

    def _cleanup_colllection_thread(self):
        """Clean up the collection thread if possible."""
        if self.collection_thread and self.collection_thread_awaitable:
            self.collection_thread.join()
            self.collection_thread = None
            self.collection_thread_awaitable = False

    def _cleanup_putter_thread(self):
        """Clean up the collection thread if possible."""
        if self.put_thread:
            self.put(None)
            self.put_thread.join()
            self.put_thread = None

    if HAVE_XDIST:
        @pytest.hookimpl(trylast=True)
        def pytest_xdist_node_collection_finished(
                self, node: str, ids: list[str]) -> None:
            """Handle a pytest-xdist worker completing its collection phase.

            I believe that this gets invoked outside of the main thread, which
            means we cannot simply use the `put` method. For now we simply
            ignore this message, leaving this as a place-holder should it
            become required in the future.
            """

    #
    # Test execution phase.
    #
    @main_process_only
    def pytest_runtest_logstart(
            self, nodeid: str, location: tuple[str, int | None, str]) -> None:
        """Log the start of a test.

        Since there is no definitive hook indicating the start of the run
        phase, this method infers it.
        """
        self.started_count += 1
        #print(f'Test start: {nodeid}', file=dlog)
        if not self.run_phase_started and not self.collection_active:
            self.put('proto_start_run_phase')
            self.run_phase_started = True
        self.put('proto_start_test', clean_nodeid(nodeid))
        self._cleanup_colllection_thread()

    @main_process_only
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Log a report for a test run."""
        report.nodeid = clean_nodeid(report.nodeid)
        self.put('proto_test_report', report)
        #if report.outcome == 'failed':
        #    print('Pipe: ...test report:', report, file=dlog)
        #    print('...', report.capstdout, file=dlog)

    @main_process_only
    def pytest_runtest_logfinish(
            self, nodeid: str, location: tuple[str, int | None, str]) -> None:
        """Log the completion of a test."""
        if in_main_thread():
            # print(f'Test end: {nodeid}', file=dlog)
            self.put('proto_end_test', clean_nodeid(nodeid))
            print(f'Test finish {nodeid}', file=dlog)

    #
    # Handling of general terminal output.
    #
    def write_sep(
            self, sep: str, title: str | None = None,
            fullwidth: int | None = None, **markup: bool,
        ) -> None:
        """Process request to write a ruled separator line.

        :sep:       Nominally a separator character, but it may be more than
                    one character.
        :title:     A title to embed within the separator line.
        :fullwidth: A value to over-ride the terminal width.
        :markup:    Markup flag values.
        """
        self.put('write_sep', sep, title, fullwidth)

    def write(self, text: str | bytes, **markup: bool) -> None:
        """Process a request to write some text or bytes."""
        self.put('write', text)

    def write_line(self, line: str | bytes, **markup: bool) -> None:
        """Process a request write a line of text or bytes."""
        self.put('write_line', line)

    def rewrite(self, line: str, **markup: bool) -> None:
        """Process a request to over-write the current line.

        As far as I can tell, this is used for a "poor man's progress display".
        As such, we simply add another progress task on demand.
        """
        self.put('rewrite', line)

    #
    # Handling of general Rich terminal output.
    #
    # These methods provide additional ``TerminalWriter` functionality for
    # plugins that detect they are running under the control of APP_NAME.
    #
    def rich_write_sep(
            self, sep: str, title: str | None = None,
            fullwidth: int | None = None, **markup: bool,
        ) -> None:
        """Process request to write a ruled separator line.

        :sep:       Nominally a separator character, but it may be more than
                    one character.
        :title:     A title to embed within the separator line.
        :fullwidth: A value to over-ride the terminal width.
        :markup:    Markup flag values.
        """
        self.put('write_sep', sep, title, fullwidth)

    def rich_write(self, text: str | bytes, **markup: bool) -> None:
        """Process a request to write some text or bytes."""
        self.put('rich_write', text)

    def rich_write_line(self, line: str | bytes, **markup: bool) -> None:
        """Process a request write a line of text or bytes."""
        self.put('rich_write_line', line)

    #
    # Other hooks.
    #
    @main_process_only
    def pytest_internalerror(
            self,
            excrepr: ExceptionRepr,
            excinfo: pytest.ExceptionInfo[BaseException],
        ) -> None | bool:
        """Report an internal error."""
        self.put('pytest_internalerror')

    def pytest_warning_recorded(
            self,
            warning_message: warnings.WarningMessage,
            when: Literal['config', 'collect', 'runtest'],
            nodeid: str,
            location: tuple[str, int, str] | None,
        ) -> None:
        """Note standard Python warning."""
        filename, line_number, function = location or (None, None, None)
        args = when, nodeid, filename, line_number, function
        key = str(warning_message), *args
        args = warning_message, *args
        if key not in self.seen_warnings:
            self.put('proto_warning_recorded', *args)
            self.seen_warnings.add(key)

    @main_process_only
    def pytest_keyboard_interrupt(
            self, excinfo: pytest.ExceptionInfo[BaseException]) -> None:
        """Handle KeyboardInterrupt base exceptions.

        Note that pytest uses this for non-keyboard interruptions, such as
        errors during test collection.
        """
        self.put('pytest_keyboard_interrupt')

    #
    # Support methods for the pipe protocol.
    #
    def put(self, name: str | None, *args) -> None:
        """Put an information line to the communications pipe.

        :name:
            A name defining the type of information. The receiver interprets
            this as a method to be looked up and invoked, so conventionally
            this conforms to the Python method name conventions.
        :args:
            Values to pickled and converted to hex before writing to the pipe.
        """
        if name is None:
            self.put_queue.put(None)
        else:
            params = [encode(a) for a in args]
            self.put_queue.put(f'<<--RICH-PIPE-->>: {name} {" ".join(params)}')
        # print(f'<<--RICH-PIPE-->>{os.getpid()}: {name}', file=dlog)
        return

        if self.in_pipe_thread():
            params = [encode(a) for a in args]
            print(
                f'<<--RICH-PIPE-->>: {name} {" ".join(params)}',
                file=self.pipe)
            # print(f'<<--RICH-PIPE-->>{os.getpid()}: {name}', file=dlog)
            return True
        else:
            return False

    def put_from_queue(self) -> None:
        """Copy from put_queue to the pipe."""
        while True:
            text = self.put_queue.get()
            if text is None:
                break
            print(text, file=self.pipe)
        print(f'PUTTER: finished', file=dlog)

    def _setup_stdouts(self):
        """Set up stdout and stderr for reliable operation.

        Pytest has to have been started with sys.stdout as a pipe to its parent
        process and sys.stderr attached to the terminal as usual.
        ::

                               -----------------
            sys.stdout[1] --->                  ---> pytest-richer-app
                               -----------------

            sys.stderr[2] ---> Terminal

        This means that is Pytest or other plugins write to sys.stdout it will
        corrupt the communications with the pytest-richer-app and writing to
        sys.stderr will corrupt the application display. This re-plumbs things
        to be:
        ::

                               -----------------
            self.pipe[A]  --->                  ---> pytest-richer-app
                               -----------------

            sys.stdout -------,
                              |---> self.stdout: PipeRedirector
            sys.__stdout__ ---'

            sys.stderr -------,
                              |---> self.stderr: PipeRedirector
            sys.__stderr__ ---'

            [1] ---> os.devnull
            [2] ---> os.devnull

        Where A is a new FD, created using ``os.dup(.)``.
        """
        # Create a new file object for the pipe on stdout, making it line
        # buffered.
        self.pipe = os.fdopen(
            os.dup(1), mode='wt', encoding='utf=8', buffering=1)

        # Make sure that anything written directly to the stdout amd stderr
        # file descriptors (1 and 2) is discarded.
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)

        # Set up sys.stdout and sys.stderr to be redirected using
        # PipeRedirector objects.
        sys.stdout = sys.__stdout__ = self.stdout
        sys.stderr = sys.__stderr__ = self.stderr
