"""Script to perform a pre-relase check."""

import subprocess
import sys
from functools import partial
from pathlib import Path

run = partial(subprocess.run, capture_output=True, text=True)


def check_git_is_clean():
    """Check the the working tree is clean."""
    unstaged = run(['git', 'diff', '--quiet'])
    if unstaged.returncode:
        sys.exit('You have unstaged changes.')

    staged = run(['git', 'diff', '--quiet', '--cached'])
    if staged.returncode:
        sys.exit('You have staged changes.')


def check_version():
    """Check that version information matches and is correct."""
    pyproject = Path('pyproject.toml')
    readme = Path('README.rst')
    pyproject_version = readme_version = ''
    for line in pyproject.read_text(encoding='utf-8').splitlines():
        if line.startswith('version = "'):
            _, _, rem = line.partition('"')
            pyproject_version, *_ = rem.partition('"')
            break
    for line in readme.read_text(encoding='utf-8').splitlines():
        if line.startswith('The most recent release is '):
            *_, readme_version = line.rpartition(' ')
            readme_version = readme_version.strip()[:-1]
            break
    if not pyproject_version:
        sys.exit(f'Could not find version in {pyproject}')
    if not readme_version:
        sys.exit(f'Could not find version in {readme}')
    if pyproject_version != readme_version:
        sys.exit(f'Versions to not match in {pyproject} and {readme}')

    version_tag = f'v{pyproject_version}'
    tags_text = run(['git', 'show-ref', '--tags']).stdout.splitlines()
    tag_tuples = [line.rpartition('/') for line in tags_text]
    tags = {c: a.split()[0] for a, b, c in tag_tuples}
    if version_tag not in tags:
        sys.exit(f'There is no tag for version {pyproject_version}')

    head = run(['git', 'rev-parse', 'HEAD']).stdout.strip()
    if head != tags[version_tag]:
        sys.exit(f'HEAD does not match tags for {pyproject_version}')


check_git_is_clean()
check_version()
