"""Temperature-only experiment: two fixed prompts and configurable repeats."""

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aiac.config import Settings
from aiac.llm import reasoning
from aiac.tasks.week_01.day_02 import strict_json

MODEL = "gpt-6-luna"
TEMPERATURES = (0.0, 0.7, 1.2)
TASKS = {
    "price": {
        "title": "Покупка",
        "prompt": "В корзине 3 товара по 1290 рублей и 2 товара по 875 рублей. "
        "Если исходная сумма товаров не меньше 5000 рублей, сначала действует скидка 15%. "
        "Затем купон уменьшает полученную стоимость товаров ещё на 12%. "
        "После обеих скидок стоимость товаров округляют вверх до целого рубля "
        "(промежуточного округления нет). Доставка бесплатна, если округлённая стоимость "
        "товаров не меньше 4500 рублей, иначе стоит 290 рублей. "
        "Сколько рублей нужно заплатить вместе с доставкой? "
        "Верни только JSON с двумя строковыми полями answer и explanation. "
        "В answer укажи итоговое целое число без единиц измерения, "
        "в explanation — краткое объяснение. Без Markdown и дополнительных полей.",
    },
    "names": {
        "title": "Названия приложения",
        "prompt": "Придумай пять разных названий приложения для ухода за домашними "
        "растениями. Каждое название должно состоять ровно из двух русских слов: "
        "только русские буквы, разделённые одним пробелом, без цифр и знаков препинания. "
        'Верни только JSON вида {"names":["Первое название", ...]} с пятью названиями. '
        "Без Markdown, пояснений и дополнительных полей.",
    },
}


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: Literal["all", "price", "names"] = "all"
    temperature: Literal["all", "0", "0.7", "1.2"] = "all"
    repeats: int = Field(default=5, ge=1, le=10, strict=True)


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: Literal["price", "names"]
    temperature: float
    repeat: int = Field(ge=1, le=10, strict=True)

    @field_validator("temperature", mode="before")
    @classmethod
    def supported_temperature(cls, value: Any) -> float:
        if type(value) not in (int, float) or value not in TEMPERATURES:
            raise ValueError("Допустимы только температуры 0, 0.7 и 1.2.")
        return float(value)


def plan(config: Selection) -> dict[str, Any]:
    tasks = TASKS if config.task == "all" else {config.task: TASKS[config.task]}
    temperatures = TEMPERATURES if config.temperature == "all" else (float(config.temperature),)
    runs = [
        {"task": task, "temperature": temperature, "repeat": repeat}
        for task in tasks
        for temperature in temperatures
        for repeat in range(1, config.repeats + 1)
    ]
    return {
        "version": 1,
        "model": MODEL,
        "reasoning": "none",
        "runs": runs,
        "calls": len(runs),
        "repeats": config.repeats,
        "prompts": TASKS,
        "max_output_tokens": 1024,
        "timeout_seconds": 60,
        "created_at": datetime.now(UTC).isoformat(),
    }


def payload(config: RunInput) -> dict[str, Any]:
    return {
        "model": MODEL,
        "input": TASKS[config.task]["prompt"],
        "temperature": config.temperature,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 1024,
        "store": False,
    }


def evaluate(text: str, task: str) -> dict[str, Any]:
    import re

    try:
        data = strict_json(text)
    except (ValueError, TypeError):
        return {
            "format_valid": False,
            "correct": False if task == "price" else None,
            "normalized": None,
            "names": [],
            "evaluation": "Невалидный JSON.",
        }
    if task == "price":
        valid = (
            isinstance(data, dict)
            and set(data) == {"answer", "explanation"}
            and isinstance(data["answer"], str)
            and re.fullmatch(r"[0-9]+", data["answer"]) is not None
            and isinstance(data["explanation"], str)
            and bool(data["explanation"].strip())
        )
        correct = valid and data["answer"] == "4494"
        return {
            "format_valid": bool(valid),
            "correct": bool(correct),
            "normalized": data["answer"] if valid else None,
            "names": [],
            "evaluation": "Верно." if correct else "Ошибка формата или неверный итог.",
        }
    raw_names = data.get("names") if isinstance(data, dict) else None
    names = raw_names if isinstance(raw_names, list) else []
    strings = isinstance(raw_names, list) and all(isinstance(name, str) for name in names)
    normalized = [name.casefold() for name in names] if strings else []
    valid = (
        isinstance(data, dict)
        and set(data) == {"names"}
        and strings
        and len(names) == 5
        and len(set(normalized)) == 5
        and all(re.fullmatch(r"[А-Яа-яЁё]+ [А-Яа-яЁё]+", name) for name in names)
    )
    return {
        "format_valid": bool(valid),
        "correct": None,
        "normalized": sorted(normalized) if valid else None,
        "names": normalized if valid else [],
        "evaluation": "Формат соблюдён; качество названий оцените при просмотре."
        if valid
        else "Нужны пять разных названий из двух русских слов каждое.",
    }


async def execute(config: RunInput, settings: Settings) -> dict[str, Any]:
    request = payload(config)
    result = await reasoning.complete(request, settings, timeout_seconds=60)
    row = {
        **config.model_dump(),
        **asdict(result),
        "request": request,
        "usage": None,
        "format_valid": None,
        "correct": None,
        "normalized": None,
        "names": [],
        "created_at": datetime.now(UTC).isoformat(),
    }
    if result.status != "api_error" and result.response is not None:
        row["usage"] = result.response.get("usage")
    if result.status == "completed":
        row.update(evaluate(result.text, config.task))
    return row
