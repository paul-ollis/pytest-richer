#!/bin/bash

cp resources/*.py demo-tests/
cp resources/test_clipboard.py-capture demo-tests/test_clipboard.py
cp -r resources/usability demo-tests
cp -r resources/regression demo-tests
PYTEST=$(which pytest)
$COV $PYTEST demo-tests --rich --tb=short -n16
