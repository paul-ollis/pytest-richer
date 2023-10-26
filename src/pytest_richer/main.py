"""Main module for the pytest-richer front-end application."""
from __future__ import annotations

import argparse
from pathlib import Path

import rich

import pytest_richer
pytest_richer.Logger.new(name='main', path_str='main')
from pytest_richer.tui import app as tui_app, configuration

rich.traceback.install(show_locals=False)


def create_arg_parser():
    """Create the command line argument parser."""
    parser = argparse.ArgumentParser('A pytest execution environment.')
    parser.add_argument(
        '--profile', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument(
        '--hid_display', action='store_true' ,help=argparse.SUPPRESS)
    return parser


def main() -> None:
    """Run the richer pytext front-end application."""
    parser = create_arg_parser()
    args = parser.parse_args()
    configuration.init(Path('.'))
    app = tui_app.PytestApp(args=args, config=configuration.config())
    app.run()


if __name__ == '__main__':
    main()
