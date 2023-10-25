#!/bin/bash

cp resources/*.py demo-tests/
cp resources/test_clipboard.py-capture demo-tests/test_clipboard.py
PYTEST=$(which pytest)
export PYTEST_RICHER_FORCE_ERROR='oops'
$COV $PYTEST demo-tests --rich --tb=short
