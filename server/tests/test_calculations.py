from __future__ import annotations

import asyncio
import json

from app.agent.calculations import (
    add_days,
    convert_units,
    days_between,
    safe_eval,
)
from app.agent.tools import CalculatorTool, DateAddTool, UnitConvertTool


def test_safe_eval() -> None:
    assert safe_eval("2 + 3 * 4") == 14
    assert safe_eval("(1200 - 800) / 800 * 100") == 50.0
    assert safe_eval("-3 + 10") == 7


def test_convert_units() -> None:
    assert convert_units(1, "公斤", "斤") == 2.0
    assert convert_units(2, "斤", "kg") == 1.0
    assert convert_units(5000, "步", "公里") == 3.5
    assert convert_units(3.5, "公里", "步") == 5000.0
    assert convert_units(1, "km", "m") == 1000.0


def test_date_arithmetic() -> None:
    assert add_days("2026-08-23", 7).isoformat() == "2026-08-30"
    assert add_days("2026-08-23", -3).isoformat() == "2026-08-20"
    assert days_between("2026-08-23", "2026-08-30") == 7


def test_calculator_tool() -> None:
    payload = json.loads(asyncio.run(CalculatorTool().execute(expression="2 + 3 * 4")))
    assert payload["result"] == 14


def test_unit_convert_tool() -> None:
    payload = json.loads(
        asyncio.run(
            UnitConvertTool().execute(value=1, from_unit="公斤", to_unit="斤")
        )
    )
    assert payload["result"] == 2.0


def test_date_add_tool() -> None:
    payload = json.loads(
        asyncio.run(DateAddTool().execute(date="2026-08-23", days=7))
    )
    assert payload["date"] == "2026-08-30"
