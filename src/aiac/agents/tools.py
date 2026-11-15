"""Three local tools; no eval, external data sources or application credentials."""

import ast
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CalculatorInput(ToolInput):
    expression: str = Field(min_length=1, max_length=200)


class TimeInput(ToolInput):
    timezone: Literal["UTC", "Europe/Moscow"]


class ExchangeInput(ToolInput):
    amount: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)
    source: Literal["USD", "EUR", "RUB"]
    target: Literal["USD", "EUR", "RUB"]


def calculate(expression: str) -> dict[str, str]:
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 60:
        raise ValueError("Слишком сложное выражение.")

    def visit(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = Decimal(str(node.value))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            value = visit(node.left) + visit(node.right)
        else:
            raise ValueError("Разрешены только неотрицательные числа, скобки и сложение.")
        if not value.is_finite() or abs(value) > Decimal("1e12"):
            raise ValueError("Число вне допустимого диапазона.")
        return value

    return {"value": str(visit(tree.body))}


def current_time(timezone: str) -> dict[str, str]:
    return {"timezone": timezone, "time": datetime.now(ZoneInfo(timezone)).isoformat()}


def fixed_exchange(amount: float, source: str, target: str) -> dict[str, str]:
    rates = {"RUB": Decimal("1"), "USD": Decimal("90"), "EUR": Decimal("100")}
    value = Decimal(str(amount)) * rates[source] / rates[target]
    return {
        "amount": str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "currency": target,
        "rate": str(rates[source] / rates[target]),
        "notice": "Учебный фиксированный курс: 1 USD = 90 RUB, 1 EUR = 100 RUB; не рыночный.",
    }


INPUTS: dict[str, type[ToolInput]] = {
    "calculator": CalculatorInput,
    "current_time": TimeInput,
    "fixed_exchange": ExchangeInput,
}
DESCRIPTIONS = {
    "calculator": (
        "Вычислить выражение: неотрицательные числа, скобки и только сложение (+). "
        "Без кода и функций."
    ),
    "current_time": "Получить текущее время UTC или Europe/Moscow.",
    "fixed_exchange": "Конвертировать USD, EUR и RUB по фиксированному курсу внутри функции.",
}
SCHEMAS = [
    {
        "type": "function",
        "name": name,
        "description": DESCRIPTIONS[name],
        "parameters": schema.model_json_schema(),
        "strict": True,
    }
    for name, schema in INPUTS.items()
]


def execute_tool(name: str, arguments: str) -> dict[str, Any]:
    if name not in INPUTS:
        return {"ok": False, "error": "Неизвестный инструмент."}
    if len(arguments) > 2000:
        return {"ok": False, "error": "Слишком длинные аргументы инструмента."}
    try:
        values = INPUTS[name].model_validate_json(arguments)
        if isinstance(values, CalculatorInput):
            result = calculate(values.expression)
        elif isinstance(values, TimeInput):
            result = current_time(values.timezone)
        else:
            assert isinstance(values, ExchangeInput)
            result = fixed_exchange(values.amount, values.source, values.target)
        return {"ok": True, "result": result}
    except (ValidationError, ValueError, ArithmeticError, SyntaxError, RecursionError):
        return {"ok": False, "error": "Некорректные аргументы или недопустимое вычисление."}
