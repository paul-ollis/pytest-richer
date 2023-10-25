#!/usr/bin/env python
"""Run the exercise tests."""
from __future__ import annotations

# pylint: disable=redefined-builtin
# ruff: noqa: A001, FBT002

import os
import shutil
import subprocess
from functools import partial
from pathlib import Path
from typing import Annotated

from typer import Option, Typer


execute = partial(subprocess.run, capture_output=False, text=True)
exercises_path = Path('exercises')

app = Typer()


def prepare_coverage():
    """Prepare to measure coverage."""
    execute(['coverage', 'erase'])


def prepare_demo_tests_directory():
    """Prepare the demo-tests directory before the test run."""
    demo_dir = Path('demo-tests')
    for path in demo_dir.glob('*'):
        if path.name in ('README.rst', '.gitignore'):
            continue
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def finish_coverage():
    """Finish measuring coverage."""
    execute(['coverage', 'report'])
    execute(['coverage', 'json'])
    execute(['py-cov-combine'])


@app.command()
def list():
    """List the available test exercises."""
    for path in exercises_path.glob('*.sh'):
        print(path.name)


@app.command()
def run(
        sel: Annotated[str, Option(
            help='Select a given exercise, as shown by list command.')] = '',
        cov: Annotated[bool, Option(
            help='Run with coverage.')] = False,
    ):
    """Run some or all of the test exercises."""
    if cov:
        prepare_coverage()
        os.environ['COV'] = 'coverage run --append'
    for path in exercises_path.glob('*.sh'):
        if not sel or sel == path.name:
            full_path = path.resolve()
            prepare_demo_tests_directory()
            execute([str(full_path)])
    if cov:
        finish_coverage()


if __name__ == '__main__':
    app()
