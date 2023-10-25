#!/bin/bash

cp resources/*.py demo-tests/
PYTEST=$(which pytest)
$COV $PYTEST demo-tests --rich -n16 --showlocals --tb=long
