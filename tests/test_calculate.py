"""Unit tests for safe arithmetic calculate function."""
import pytest
from agents.tools import calculate


def test_calculate_valid_expressions():
    assert abs(calculate("120 - 105.6") - 14.4) < 1e-9
    assert abs(calculate("(40 + 25) / 10.4") - 6.25) < 1e-9
    assert calculate("-3 * 2") == -6.0
    assert calculate("-(10 + 5)") == -15.0
    assert calculate("100 / 4 * 2") == 50.0


def test_calculate_rejects_names_and_variables():
    with pytest.raises(ValueError, match="Unsupported syntax|invalid"):
        calculate("x + 1")
    with pytest.raises(ValueError):
        calculate("True")


def test_calculate_rejects_function_calls():
    with pytest.raises(ValueError, match="Unsupported syntax|invalid"):
        calculate("abs(-5)")
    with pytest.raises(ValueError):
        calculate("print(1)")


def test_calculate_rejects_imports():
    with pytest.raises(ValueError):
        calculate("__import__('os')")


def test_calculate_rejects_attribute_access():
    with pytest.raises(ValueError):
        calculate("math.pi")
    with pytest.raises(ValueError):
        calculate("(1).__class__")


def test_calculate_rejects_exponent_abuse():
    with pytest.raises(ValueError, match="Unsupported binary operator"):
        calculate("2 ** 10")
    with pytest.raises(ValueError):
        calculate("2 ** 1000000")


def test_calculate_rejects_zero_division():
    with pytest.raises(ValueError, match="Division by zero"):
        calculate("10 / 0")
