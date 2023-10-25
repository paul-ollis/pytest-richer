#!/bin/bash

cp resources/*.py demo-tests/
cp resources/test_clipboard.py-capture demo-tests/test_clipboard.py
PYTEST=$(which pytest)
$COV $PYTEST demo-tests --rich --tb=short -n16 --show-capture=log
