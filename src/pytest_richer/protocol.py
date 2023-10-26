"""Message protocol used to send information from a running pytest."""
from __future__ import annotations
# pylint: disable=too-few-public-methods
# ruff: noqa: ANN401

import ast
import pickle
import types
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, ClassVar, TYPE_CHECKING

import pytest
import pytest_asyncio
from _pytest import nodes
from rich.pretty import Node

import pytest_richer
from pytest_richer.tui.test_types import TestID

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from typing_extensions import Self

    from rich.traceback import Frame, Stack, Trace

dlog = pytest_richer.get_log('main')

# The pytest Config.rootpath. This is initialised to Path.cwd(), but is
# updated whenever a pytest.Config object is decoded.
rootpath: Path = Path.cwd()


def repr_error(obj: Any, add_msg:str = '') -> RuntimeError:
    """Create a RuntimeError for an unrepresentable value."""
    msg = f'Cannot represent {obj.__class__.__name__}'
    if add_msg:
        msg += f'\n{add_msg}'
    return RuntimeError(msg)


def repr_type_error(obj: Any) -> RuntimeError:
    """Create a TypeError for an unexpected value."""
    msg = f'Unsupported type {obj.__class__.__name__}'
    return TypeError(msg)


def drop(_v) -> None:
    """Drop a value, replacing it with ``None``."""


def identity(v):
    """Leave a value unchanged."""
    return v


def optional_value(v: Any) -> Any:
    """Convert a an optional value."""
    if v is None:
        return None
    else:
        handler = handler_map.get(type(v), None)
        if handler:
            return handler(v)
        else:
            raise repr_error(v)


def result_list(v: list[pytest.Node]) -> list[NodeRepresentation]:
    """Convert a result list to its `Representation` form."""
    ret = []
    for el in v:
        handler = handler_map.get(type(el), None)
        if handler:
            ret.append(handler(el))
        else:
            msg = f'Protocol: Cannot encode result_list {el=}'
            msg += f'\ntype={type(el)}'
            raise repr_error(el, msg)
    return ret


class Representation:
    """Base for simplified representations of pytest objects."""

    _type = type(None)
    _ctor: None | Callable = None
    _attrs: tuple[str, Callable[[Any], Any]] = ()
    _init_args: frozenset[str] | None = None
    _default_args: ClassVar[dict[str, Any]] = {}

    def __init__(self, attrs: dict):
        self.__dict__.update(attrs)

    @classmethod
    def encode(cls, obj: Self) -> Self:
        """Encode an object as its representaion."""
        kw = {}
        for name, handler in cls._attrs:
            with suppress(AttributeError):
                kw[name] = handler(getattr(obj, name))
        return cls(kw)


class NodeRepresentation(Representation):
    """A representation of a _pytest.nodes.Node."""

    _type = nodes.Node
    _attrs = (
        ('name', identity),
        ('parent', optional_value),
        ('config', optional_value),
        ('session', optional_value),
        ('fspath', drop),
        ('path', identity),
        ('nodeid', identity),
    )
    _ctor = nodes.Node.from_parent
    _init_args = frozenset(
        n for n, _ in _attrs if n not in ('session',))


class CollectorRepresentation(NodeRepresentation):
    """A representation of a pytest.Collector."""

    _type = pytest.Collector


class ItemRepresentation(NodeRepresentation):
    """A representation of a pytest.Item."""

    _type = pytest.Item


class ModuleRepresentation(NodeRepresentation):
    """A representation of a pytest.Module."""

    _type = pytest.Module
    _ctor = pytest.Module.from_parent
    _init_args = frozenset(
        n for n, _ in NodeRepresentation._attrs                  # noqa: SLF001
        if n not in ('config', 'session'))


class FunctionRepresentation(NodeRepresentation):
    """A representation of a pytest.Function."""

    _type = pytest.Function
    _attrs = NodeRepresentation._attrs + (                # noqa: RUF005,SLF001
        ('callobj', identity),
        ('originalname', identity),
    )
    _ctor = pytest.Function.from_parent
    _init_args = frozenset(
        n for n, _ in _attrs
        if n not in ('config', 'session'))


class CoroutineRepresentation(FunctionRepresentation):
    """A representation of a pytest_asyncio Coroutine."""

    _type = pytest_asyncio.plugin.Coroutine


class PackageRepresentation(NodeRepresentation):
    """A representation of a pytest.Package."""

    _type = pytest.Package


# TODO: This is very much work-in-progress.
class DirRepresentation(NodeRepresentation):
    """A representation of a pytest.Dir."""

    _type = pytest.Dir


class ClassRepresentation(NodeRepresentation):
    """A representation of a pytest.Class."""

    _type = pytest.Class


class ConfigRepresentation(Representation):
    """A representation of the pytest.Confg."""

    _rootpath: Path
    _type = pytest.Config
    _attrs = (
        ('name', identity),
        ('parent', optional_value),
        ('session', optional_value),
        ('fspath', drop),
        ('path', identity),
        ('option', identity),
        ('_rootpath', identity),
    )
    _init_args = frozenset({'pluginmanager'})
    _default_args: ClassVar[dict[str, Any]] = {
        'pluginmanager': pytest.PytestPluginManager(),
    }
    option: pytest.Confg

    @property
    def rootpath(self):
        """The root path for the loaded tests."""
        return self._rootpath

    #: @property
    #: def option(self):
    #:     """The parsed command line options."""
    #:     return config.option

    def getoption(self, name: str, default=None):
        """Get the value of an option."""
        return getattr(self.option, name, default)


class CollectReportRepresentation(Representation):
    """A representation of a pytest.CollectReport."""

    _type = pytest.CollectReport
    _attrs = (
        ('nodeid', identity),
        ('outcome', identity),
        ('longrepr', drop),
        ('when', identity),
        ('result', result_list),
        ('sections', identity),
        ('pickled_rich_traceback', identity),
    )

    @property
    def rich_traceback(self):
        if self.pickled_rich_traceback is None:
            return None

        _rtb = getattr(self, '_rich_traceback', None)
        if _rtb is None:
            self._rich_traceback = pickle.loads(
                self.pickled_rich_traceback)                       # noqa: S301
        return self._rich_traceback


class TestReportRepresentation(NodeRepresentation):
    """A representation of a pytest.TestReport."""

    _type = pytest.TestReport
    _attrs = (
        ('duration', identity),
        ('keywords', drop),
        ('location', identity),
        ('longrepr', drop),
        ('nodeid', identity),
        ('outcome', identity),
        ('pickled_rich_traceback', identity),
        ('sections', identity),
        ('start', identity),
        ('stop', identity),
        ('user_properties', drop),
        ('wasxfail', identity),
        ('when', identity),
        ('worker_id', identity),
    )

    @property
    def rich_traceback(self):
        if self.pickled_rich_traceback is None:
            return None

        _rtb = getattr(self, '_rich_traceback', None)
        if _rtb is None:
            self._rich_traceback = pickle.loads(
                self.pickled_rich_traceback)                       # noqa: S301
        return self._rich_traceback


class SessionRepresentation(Representation):
    """A representation of a pytest.Session."""

    _type = pytest.Session
    _attrs = (
        ('config', optional_value),
    )
    #: _ctor = get_session
    _init_args = frozenset(['config'])


def repr_object(v: Any) -> Any:
    """Convert an object to a `Representation` based instance if possible."""
    if v is None:
        return v

    handler = handler_map.get(type(v))
    if handler:
        return handler(v)
    else:
        return v


def repr_sequence(s: Sequence) -> tuple | list:
    """Convert a sequence a `Representation` based instances if possible."""
    ret = [repr_object(el) for el in s]
    if isinstance(s, tuple):
        return tuple(ret)
    else:
        return ret


def encode(obj: Any) -> str:
    """Encode an object.

    The provided object may first be converted to a `Representation` based
    object. The object is then pickled and the result converted to hexadecimal.
    """
    representation = repr_object(obj)
    return pickle.dumps(representation).hex()


def decode(enc: str) -> Any:
    """Decode an object.

    The string is converted from hex to bytes then unpickled. The resuting
    value may be a `Representation` based object or contain such objects.
    """
    try:
        obj = pickle.loads(bytes.fromhex(enc))                     # noqa: S301
    except ValueError as e:
        s = str(e)
        if s.startswith('non-hexadecimal number found in fromhex'):
            p = int(s.rpartition(' ')[-1])
            a, b = enc[:p], enc[p:]
            print('Non-hex encoded data received:', file=dlog)
            print('  Before::', file=dlog)
            while a:
                print(f'    {a[:60]}', file=dlog)
                a = a[60:]
            print('  After::', file=dlog)
            while b:
                print(f'    {b[:60]}', file=dlog)
                b = b[60:]
        raise

    convert_nodeids(obj)
    return obj


def convert_nodeids(obj: Any):
    """Convert nodeid strings, within an object, to TestID instances."""
    # pylint: disable=global-statement
    if isinstance(obj, ConfigRepresentation):
        global rootpath                                         # noqa: PLW0603
        rootpath = obj.rootpath
        return

    simple_convertable = (
        NodeRepresentation, TestReportRepresentation,
    )
    if isinstance(obj, CollectReportRepresentation):
        obj.nodeid = TestID(obj.nodeid, rootpath=rootpath)
        for item in obj.result:
            convert_nodeids(item)
    elif isinstance(obj, simple_convertable):
        obj.nodeid = TestID(obj.nodeid, rootpath=rootpath)
    elif isinstance(obj, (list, tuple)):
        for el in obj:
            convert_nodeids(el)


class ReprWrapper:                     # pylint: disable=too-few-public-methods
    """Wrap a string thet represents a Python value.

    When formatted with ``repr()`` this produces the string's value rather than
    a representation of the string; e.g. ``a string`` rather then 'a string'.
    """

    def __init__(self, v):
        self.v = v

    def __repr__(self):
        return self.v


def denodify_report(
        report: TestReportRepresentation | CollectorRepresentation
    ) -> TestReportRepresentation | CollectorRepresentation:
    """Deconvert all Rich.Node collections and strings.

    TODO: Elaborate.
    """
    rich_traceback: Trace = getattr(report, 'rich_traceback', None)
    if rich_traceback:
        denodify_trace_collections(rich_traceback)
    return report


def denodify_collection(
        obj: Any, *, prefer_repr: bool = True,
    ) -> Any:
    """Convert a collection Node into the corresponding collection."""
    w = ReprWrapper
    if isinstance(obj, Node):
        if obj.children:
            child = obj.children[0]
            if child.key_repr:
                return {
                    w(c.key_repr): denodify_collection(c)
                    for c in obj.children}
            elif obj.open_brace == '{':
                return {denodify_collection(c) for c in obj.children}
            else:
                return [denodify_collection(c) for c in obj.children]
        elif len(obj.value_repr) >= 2:                          # noqa: PLR2004
            a, b = obj.value_repr[0], obj.value_repr[-1]
            if a in ('"', "'") and a == b:
                # Presume this to be a string.
                return ast.literal_eval(obj.value_repr)

    if prefer_repr:
        return w(obj.value_repr)
    else:
        return obj


def denodify_trace_collections(trace: Trace) -> None:
    """Deconvert all Rich.Node collections and strings.

    This walks the contents of each trace frame and converts nodes that
    represent collections to actual collections and also does the same for
    (apparent) strings. This means that the the trace can be used to construct
    Traceback instances that will take account of max_length and max_string
    when rendered.

    This is as hacky as it sounds, but seems to work.
    """
    stack: Stack
    for stack in trace.stacks:
        denodify_stack_collections(stack)


def denodify_stack_collections(stack: Stack) -> None:
    """Deconvert all Rich.Node collections and strings.

    A helper for denodify_trace_collections.
    """
    frame: Frame
    for frame in stack.frames:
        denodify_frame_collections(frame)


def denodify_frame_collections(frame: Frame) -> None:
    """Deconvert all Rich.Node collections and strings.

    A helper for denodify_stack_collections.
    """
    changed: dict[str, list | tuple | set | str] = {}
    name: str
    node: Node
    if frame.locals is not None:
        for name, node in frame.locals.items():
            replacement = denodify_collection(node, prefer_repr=False)
            if replacement is not node:
                changed[name] = replacement
        if changed:
            frame.locals.update(changed)
    else:
        frame.locals = {}


def make_handler_map(names: Iterable[str]) -> dict[type, Callable]:
    """Create the pytest type encoding handler map.

    This is invoked with the names of objects in this module, as obtained by
    ``dir()``. Each name is looked up (using ``eval()``, but it is safe) and
    any that is a `Representation` subclass is used to build the map.

    :return:
        A mapping from a ``type`` to a callable that can encode that type to a
        `Representation` subclass instance.
    """
    the_map = {}
    for name in names:
        # pylint: disable=eval-used
        cls = eval(name)                                    # noqa: PGH001,S307
        pytest_type = getattr(cls, '_type', None)
        if pytest_type:
            the_map[pytest_type] = cls.encode
    the_map[list] = repr_sequence
    the_map[tuple] = repr_sequence
    return the_map


handler_map = make_handler_map(dir())
