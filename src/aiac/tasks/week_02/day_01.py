"""Day 6: tool-calling agent and controlled resource comparison."""

import json
from dataclasses import asdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aiac.agents.simple import SimpleAgent
from aiac.config import Settings
from aiac.llm.client import ModelError, ModelResult

CONTROL_PROMPT = (
    "Переведи отдельно 100 USD и 50 EUR в рубли. Сложи полученные суммы. "
    "Укажи текущее время UTC: используй часы, если инструмент доступен, "
    "иначе дай приблизительную оценку самостоятельно. "
    "Верни только JSON-объект без Markdown: "
    '{"timestamp": "<YYYY-MM-DDTHH:mm:ssZ>", '
    '"amount_rub": <итоговое число>}. Значения в угловых скобках замени результатами. '
    "В timestamp должна быть временная метка, без null и поясняющего текста."
)
EXAMPLES = {"control": CONTROL_PROMPT}
ToolName = Literal["fixed_exchange", "calculator", "current_time"]


def default_tools() -> list[ToolName]:
    return ["fixed_exchange", "calculator", "current_time"]


class AgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=4000)
    tools: list[ToolName] = Field(default_factory=default_tools, max_length=3)


def check_json(text: str) -> dict[str, bool]:
    def reject_constant(value: str) -> None:
        raise ValueError("Non-JSON numeric constant")

    try:
        json.loads(text, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        return {"json": False}
    return {"json": True}


async def execute(config: AgentInput, settings: Settings) -> dict[str, Any]:
    result = asdict(await SimpleAgent(settings, list(config.tools)).run(config.prompt))
    result["tools"] = list(dict.fromkeys(config.tools))
    result["usage"] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        values = [(step.get("usage") or {}).get(key) for step in result["steps"]]
        known = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
        result["usage"][key] = sum(known) if values and len(known) == len(values) else None
        result["usage"][f"known_{key}"] = sum(known)
    result["tool_calls"] = sum(len(step["tools"]) for step in result["steps"])
    result["check"] = check_json(result["text"]) if result["status"] == "completed" else None
    return result


async def run(prompt: str, settings: Settings) -> ModelResult:
    raise ModelError("Используйте форму агента шестого дня.")
