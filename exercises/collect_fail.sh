#!/bin/bash

cp resources/*.py demo-tests/
cp resources/test_cat_attack.py-collect-fail demo-tests/test_cat_attack.py
PYTEST=$(which pytest)
$COV $PYTEST demo-tests --rich --tb=short -n16
