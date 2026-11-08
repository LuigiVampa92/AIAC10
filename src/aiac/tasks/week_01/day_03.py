"""Five fixed tasks, four prompting methods and two actual API reasoning efforts."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aiac.config import Settings
from aiac.llm import reasoning

MODEL = "gpt-6-luna"
TASK_SET_VERSION = "v2"
MAX_OUTPUT_TOKENS = 16000
METHODS = {
    "direct": "Прямой ответ",
    "steps": "Решай пошагово",
    "meta": "Создание промпта",
    "experts": "Группа экспертов",
}
EFFORTS = ("none", "max")
TASKS = [
    {
        "id": "price",
        "title": "Покупка",
        "answer": "4494",
        "text": "В корзине 3 товара по 1290 рублей и 2 товара по 875 рублей. "
        "Если исходная сумма товаров не меньше 5000 рублей, сначала действует скидка 15%. "
        "Затем купон уменьшает полученную стоимость товаров ещё на 12%. "
        "После обеих скидок стоимость товаров округляют вверх до целого рубля "
        "(промежуточного округления нет). Доставка бесплатна, если округлённая стоимость "
        "товаров не меньше 4500 рублей, иначе стоит 290 рублей. "
        "Сколько рублей нужно заплатить вместе с доставкой?",
    },
    {
        "id": "string",
        "title": "Строковый алгоритм",
        "answer": "c",
        "text": "В строке cabbaabbacddceffegghhijkkji удаляйте первую слева пару "
        "одинаковых соседних символов, "
        "затем начинайте просмотр сначала. Повторяйте, пока пар нет. Какая строка останется?",
    },
    {
        "id": "graph",
        "title": "Кратчайший путь",
        "answer": "12",
        "text": "Ориентированный граф, перечислены все рёбра с весами: "
        "A→B=4, A→D=2, D→B=1, B→C=5, D→E=4, E→C=1, "
        "C→F=3, E→F=2, F→G=2, B→G=4, C→G=9, D→C=9. "
        "Двигаться можно только по направлению стрелок. "
        "Какова минимальная длина пути из A в G с обязательным посещением C?",
    },
    {
        "id": "schedule",
        "title": "Расписание",
        "answer": "32",
        "text": "Встречи заданы как [начало,конец):ценность: "
        "[0,3):5, [1,4):8, [3,5):6, [4,7):10, "
        "[5,8):7, [7,9):6, [8,10):9, [9,11):8. "
        "Конец одной может совпадать с началом другой. "
        "Встречу можно посетить только целиком и не более одного раза. "
        "Какова максимальная суммарная ценность встреч без пересечений?",
    },
    {
        "id": "logic",
        "title": "Кто разбил вазу",
        "answer": "Анна",
        "text": "Вазу разбил ровно один из троих. Анна: «Это Борис». "
        "Борис: «Это не я». Вера: «Это не Анна». "
        "Ровно одно высказывание истинно. Кто разбил вазу? Ответьте именем.",
    },
]


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = "all"
    method: Literal["all", "direct", "steps", "meta", "experts"] = "all"
    effort: Literal["all", "none", "max"] = "all"


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str
    method: Literal["direct", "steps", "meta", "experts"]
    effort: Literal["none", "max"]
    stage: Literal["prepare", "solve"] = "solve"
    generated_prompt: str = Field(default="", max_length=6000)


def get_task(task_id: str) -> dict[str, str]:
    for task in TASKS:
        if task["id"] == task_id:
            return task
    raise ValueError("Неизвестная задача.")


def plan(config: Selection) -> dict[str, Any]:
    tasks = TASKS if config.task == "all" else [get_task(config.task)]
    methods = METHODS if config.method == "all" else {config.method: METHODS[config.method]}
    efforts = EFFORTS if config.effort == "all" else (config.effort,)
    runs = [
        {"task": task["id"], "method": method, "effort": effort}
        for method in methods
        for effort in efforts
        for task in tasks
    ]
    return {
        "model": MODEL,
        "task_set_version": TASK_SET_VERSION,
        "provider": "openai",
        "runs": runs,
        "solutions": len(runs),
        "calls": sum(2 if r["method"] == "meta" else 1 for r in runs),
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "timeout_seconds": 120,
        "created_at": datetime.now(UTC).isoformat(),
    }


def payload(config: RunInput) -> dict[str, Any]:
    task = get_task(config.task)
    if config.stage == "prepare":
        if config.method != "meta":
            raise ValueError("Подготовительный этап доступен только для создания промпта.")
        prompt = (
            "Составь промпт для решения следующей задачи. Верни только текст промпта, "
            "без решения и ответа. Не более 6000 символов.\nЗадача: " + task["text"]
        )
    else:
        prefix = ""
        if config.method == "steps":
            prefix = "Решай пошагово. Дай краткое объяснение решения.\n"
        elif config.method == "meta":
            if not config.generated_prompt.strip():
                raise ValueError("Сначала нужен непустой сгенерированный промпт.")
            prefix = config.generated_prompt + "\n"
        elif config.method == "experts":
            prefix = (
                "Создай группу экспертов: аналитик, инженер, критик. "
                "Каждый должен дать своё решение и ответ. Затем дай общий итог. "
                "Включи в JSON поле experts: массив из трёх объектов с полями "
                "role, solution, answer для этих трёх ролей.\n"
            )
        prompt = (
            prefix
            + "Задача: "
            + task["text"]
            + (
                "\nВерни JSON-объект с полем answer (строка с окончательным ответом, "
                "без единиц измерения). Пояснение можно поместить в explanation. Без Markdown."
            )
        )
    return {
        "model": MODEL,
        "input": prompt,
        "reasoning": {"effort": config.effort},
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "store": False,
    }


def evaluate(text: str, task_id: str, method: str) -> dict[str, Any]:
    """Exact answer comparison after whitespace/case normalization; no LLM judge."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return {"correct": False, "answer": None, "evaluation": "Ответ не является JSON."}
    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
        return {"correct": False, "answer": None, "evaluation": "Нет строкового поля answer."}
    if method == "experts":
        experts = data.get("experts")
        roles = {"аналитик", "инженер", "критик"}
        if (
            not isinstance(experts, list)
            or len(experts) != 3
            or any(
                not isinstance(e, dict)
                or any(
                    not isinstance(e.get(k), str) or not e[k].strip()
                    for k in ("role", "solution", "answer")
                )
                for e in experts
            )
            or {e["role"].strip().casefold() for e in experts} != roles
        ):
            return {
                "correct": False,
                "answer": data["answer"],
                "evaluation": "Не получены решения всех трёх экспертов.",
            }
    correct = data["answer"].strip().casefold() == get_task(task_id)["answer"].casefold()
    return {
        "correct": correct,
        "answer": data["answer"],
        "evaluation": "Верно." if correct else "Ответ не совпадает с эталоном.",
    }


async def execute(config: RunInput, settings: Settings) -> dict[str, Any]:
    request = payload(config)
    result = await reasoning.complete(request, settings)
    row = {
        "task_set_version": TASK_SET_VERSION,
        **config.model_dump(exclude={"generated_prompt"}),
        **asdict(result),
        "request": request,
        "usage": None,
        "correct": False,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if result.status != "api_error" and result.response is not None:
        row["usage"] = result.response.get("usage")
    if config.stage == "prepare":
        row["usable_prompt"] = result.status == "completed" and 0 < len(result.text.strip()) <= 6000
    elif result.status == "completed":
        row.update(evaluate(result.text, config.task, config.method))
    return row
