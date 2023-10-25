.. list-table::

   * - .. image:: resources/demo1.png

     - .. image:: resources/demo2.png


=============
Pytest-richer
=============

A replacement progress and reporting front end for `pytest`_, using `Rich`_ to
provide enhanced output.


Installation
------------

This is not yet on ``PyPi``.

Clone this repository and install using ``pip``, For example:

.. code-block::

    python -m pip install --user pytest-richer


Features
--------

- Detailed per-file/per directory progress display.

  +  Per-file view is used for preference.
  +  Per directory view is used when the number of files becomes unwieldy.

- Works very well with  `pytest-xdist`_

  + When `pytest-xdist`_ is enabled, the progress display stays the same,
    rather than switching to a simplified form.
  + Collection errors cause run to stop instead of being ignored.
  + Error reporting formatting is unaffected by `pytest-xdist`_.
  + Order of error reports are not affected by `pytest-xdist`_.

- `Rich`_ is used to format the details of collection and test execution errors.

  + Arguably more human parseable error reports (at least I think so;
    suggestions for improvements welcome).
  + In particular, collection error reports are easier to read.

- Additional information displayed.

  + `pytest-xdist`_ output displayed as part of progress display.
  + Slow setup and teardown phases are visible on the progress display.
  + Setup and teardown errors separately identified on the progress display
    and in the final summary.
  + Number of active `pytest-xdist`_ workers (parallel test count) shown.

Some other notable differences from the standard `pytest`_ reporter.

- Collection error details are reported when the ``--no-summary`` option is
  used.

- Tracebacks are not as compact when the ``--tb=short`` option is used. This
  may change in the future, but is currently not practicable.

- Long tracebacks include context lines after the failure line. This
  may change in the future, but is currently not practicable.

- Long tracebacks only show 3 lines of context before the failure line;
  `pytest`_ shows all preceding lines within the function.

There will undoubtedly be other differences and missing standard features at
this stage. Please raise an issue for any problems.


Credits
=======

This was inspired by `pytest-rich`_.

.. _pytest: https://github.com/pytest-dev/pytest
.. _pytest-rich: https://github.com/nicoddemus/pytest-rich
.. _pytest-xdist: https://github.com/pytest-dev/pytest-xdist
.. _rich: https://github.com/pytest-dev/pytest
