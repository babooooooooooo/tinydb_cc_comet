"""Collected pytest module for the E2E golden SQL runner.

The fixture lives in ``conftest.py``; without an actual test that
references ``golden_sql``, pytest would not parametrize the runner.
This module is the single entry point that asks pytest to materialise
each discovered SQL scenario as one collected test case.
"""
import pytest


@pytest.mark.e2e
def test_golden_sql(golden_sql):
    """Byte-compare runner output against the matching .expected.txt."""
    _, actual, expected = golden_sql
    assert actual == expected
