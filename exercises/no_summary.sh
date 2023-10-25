#!/bin/bash

cp resources/*.py demo-tests/
PYTEST=$(which pytest)
$COV $PYTEST demo-tests --rich --tb=short -n16 --showlocals --no-summary
