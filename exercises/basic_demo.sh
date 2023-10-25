#!/bin/bash

cp resources/*.py demo-tests/
cp resources/test_moving.py-snap1 demo-tests/test_moving.py
PYTEST=$(which pytest)
export PYTEST_RICHER_SNAP=yes
$COV $PYTEST demo-tests --rich --tb=short -n16 --showlocals
