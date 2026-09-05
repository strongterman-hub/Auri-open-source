from __future__ import annotations

import ast
import operator as _operator
from datetime import date, timedelta


_BINOPS = {
    ast.Add: _operator.add,
    ast.Sub: _operator.sub,
    ast.Mult: _operator.mul,
    ast.Div: _operator.truediv,
    ast.FloorDiv: _operator.floordiv,
    ast.Mod: _operator.mod,
    ast.Pow: _operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: _operator.pos,
    ast.USub: _operator.neg,
}


def _eval_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("Unsupported arithmetic expression.")


def safe_eval(expression: str) -> int | float:
    """Evaluate a numeric arithmetic expression without ``eval``."""
    value = (expression or "").strip()
    if not value:
        raise ValueError("Expression must not be empty.")
    try:
        tree = ast.parse(value, mode="eval")
        result = _eval_node(tree)
    except (SyntaxError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError(f"Invalid arithmetic expression: {exc}") from exc
    if isinstance(result, float):
        return round(result, 10)
    return result


_LENGTH_TO_METERS: dict[str, float] = {
    "m": 1.0,
    "meter": 1.0,
    "meters": 1.0,
    "米": 1.0,
    "km": 1000.0,
    "kilometer": 1000.0,
    "kilometers": 1000.0,
    "公里": 1000.0,
    "mi": 1609.344,
    "mile": 1609.344,
    "miles": 1609.344,
    "英里": 1609.344,
    "ft": 0.3048,
    "foot": 0.3048,
    "feet": 0.3048,
    "英尺": 0.3048,
}

_WEIGHT_TO_KG: dict[str, float] = {
    "kg": 1.0,
    "kilogram": 1.0,
    "kilograms": 1.0,
    "公斤": 1.0,
    "千克": 1.0,
    "g": 0.001,
    "gram": 0.001,
    "grams": 0.001,
    "克": 0.001,
    "jin": 0.5,
    "斤": 0.5,
    "lb": 0.45359237,
    "pound": 0.45359237,
    "pounds": 0.45359237,
    "磅": 0.45359237,
}

_STEP_UNITS = {"step", "steps", "步"}


def _length_to_meters(value: float, unit: str) -> float:
    if unit not in _LENGTH_TO_METERS:
        raise ValueError(f"Unknown length unit '{unit}'.")
    return value * _LENGTH_TO_METERS[unit]


def _meters_to_length(meters: float, unit: str) -> float:
    if unit not in _LENGTH_TO_METERS:
        raise ValueError(f"Unknown length unit '{unit}'.")
    return meters / _LENGTH_TO_METERS[unit]


def convert_units(
    value: float,
    from_unit: str,
    to_unit: str,
    step_length_m: float = 0.7,
) -> float:
    """Convert between supported length/weight units, including steps."""
    f = (from_unit or "").strip().lower()
    t = (to_unit or "").strip().lower()

    if f in _STEP_UNITS or t in _STEP_UNITS:
        if step_length_m <= 0:
            raise ValueError("step_length_m must be positive.")
        meters = (
            value * step_length_m
            if f in _STEP_UNITS
            else _length_to_meters(value, f)
        )
        result = (
            meters / step_length_m
            if t in _STEP_UNITS
            else _meters_to_length(meters, t)
        )
        return round(result, 6)

    if f in _WEIGHT_TO_KG and t in _WEIGHT_TO_KG:
        return round(value * _WEIGHT_TO_KG[f] / _WEIGHT_TO_KG[t], 6)

    if f in _LENGTH_TO_METERS and t in _LENGTH_TO_METERS:
        return round(_meters_to_length(_length_to_meters(value, f), t), 6)

    raise ValueError(f"Unsupported conversion '{from_unit}' -> '{to_unit}'.")


def add_days(date_str: str, days: int) -> date:
    return date.fromisoformat(date_str) + timedelta(days=days)


def days_between(from_str: str, to_str: str) -> int:
    return (date.fromisoformat(to_str) - date.fromisoformat(from_str)).days
