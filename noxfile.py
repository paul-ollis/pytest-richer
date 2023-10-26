"""Nox configuration."""

import nox                                       # pylint: disable=import-error


@nox.session(reuse_venv=True)
def release(session):
    """Generate a release."""
    session.install(
        'build',
        'rich',
        'pytest',
    )
    session.run('python', 'tools/pre-release-check.py')
    session.run('python', '-m', 'build')
