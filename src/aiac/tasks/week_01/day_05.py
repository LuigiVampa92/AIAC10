"""Compare three user-selected models on the same five tasks."""

import math
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aiac.config import Settings
from aiac.llm import reasoning
from aiac.tasks.week_01 import day_03

DEFAULTS = {"small": "gpt-6-luna", "medium": "gpt-6.1-sol", "flagship": "gpt-6-astra"}
ROLES = {"small": "Маленькая", "medium": "Средняя", "flagship": "Флагманская"}
SOURCE = "https://developers.openai.com/api/docs/pricing"
PRICE_DATE = "2026-11-09"
# Standard, short-context USD / 1M tokens. Exact IDs only; no guessed alias prices.
PRICES = {
    "gpt-6-luna": (0.1, 0.01, 0.5),
    "gpt-6.1-sol": (2.0, 0.1, 10.0),
    "gpt-6-astra": (10.0, 1.0, 50.0),
    "gpt-4.1": (2.0, 0.5, 8.0),
    "gpt-4.1-2025-04-14": (2.0, 0.5, 8.0),
    "gpt-4.1-mini": (0.4, 0.1, 1.6),
    "gpt-4.1-mini-2025-04-14": (0.4, 0.1, 1.6),
    "gpt-4o": (2.5, 1.25, 10.0),
    "gpt-4o-mini": (0.15, 0.075, 0.6),
}


class Rates(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    input: float = Field(ge=0)
    cached: float = Field(ge=0)
    output: float = Field(ge=0)
    source: str = Field(min_length=1, max_length=500)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    small: ModelSpec
    medium: ModelSpec
    flagship: ModelSpec
    repeats: int = Field(default=1, ge=1, le=10, strict=True)

    @model_validator(mode="after")
    def distinct(self) -> "Selection":
        if len({self.small.model, self.medium.model, self.flagship.model}) != 3:
            raise ValueError("Выберите три разные модели.")
        return self


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["small", "medium", "flagship"]
    spec: ModelSpec
    task: str
    repeat: int = Field(ge=1, le=10, strict=True)


def default_rates(model: str) -> dict[str, Any] | None:
    rates = PRICES.get(model)
    return (
        (
            dict(zip(("input", "cached", "output"), rates, strict=True))
            | {
                "source": (
                    f"https://developers.openai.com/api/docs/models/{model.split('-2025')[0]}"
                    if model.startswith("gpt-4")
                    else SOURCE
                ),
                "date": PRICE_DATE,
            }
        )
        if rates
        else None
    )


def reasoning_effort(model: str) -> str | None:
    """Fixed low for reasoning families; legacy text families receive no parameter.

    Unknown IDs retain provider defaults. Actual request is always visible;
    unsupported low is an error, never silently retried with another effort.
    """
    base = model.split(":", 2)[1] if model.startswith("ft:") else model
    if base.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")) and not base.startswith("o1-mini"):
        return "low"
    return None


def plan(config: Selection) -> dict[str, Any]:
    specs = {
        role: {
            "model": getattr(config, role).model,
            "effort": reasoning_effort(getattr(config, role).model),
            "rates": default_rates(getattr(config, role).model),
        }
        for role in ROLES
    }
    # Interleave models per task to avoid placing an entire model at one end of the run.
    runs = [
        {"task": task["id"], "role": role, "spec": {"model": spec["model"]}, "repeat": repeat}
        for repeat in range(1, config.repeats + 1)
        for task in day_03.TASKS
        for role, spec in specs.items()
    ]
    return {
        "runs": runs,
        "models": specs,
        "calls": len(runs),
        "repeats": config.repeats,
        "tasks": day_03.TASKS,
        "task_set_version": day_03.TASK_SET_VERSION,
        "max_output_tokens": 16000,
        "timeout_seconds": 120,
        "created_at": datetime.now(UTC).isoformat(),
    }


def payload(config: RunInput) -> dict[str, Any]:
    task = day_03.get_task(config.task)
    result: dict[str, Any] = {
        "model": config.spec.model,
        "input": task["text"]
        + "\nВерни JSON с полями answer (строка с итогом без единиц измерения) "
        "и explanation (краткое объяснение). Без Markdown.",
        "max_output_tokens": 16000,
        "service_tier": "default",
        "store": False,
    }
    effort = reasoning_effort(config.spec.model)
    if effort is not None:
        result["reasoning"] = {"effort": effort}
    return result


def cost(usage: dict[str, Any] | None, rates: Rates | None) -> float | None:
    if usage is None or rates is None:
        return None
    incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
    if (
        not isinstance(incoming, int)
        or isinstance(incoming, bool)
        or incoming < 0
        or not isinstance(outgoing, int)
        or isinstance(outgoing, bool)
        or outgoing < 0
    ):
        return None
    details = usage.get("input_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if type(cached) is not int or cached < 0 or cached > incoming:
        return None
    # Cache-write billing is not inferred from the read-cache count.
    if usage.get("cache_creation_input_tokens", 0) or (
        isinstance(details, dict) and details.get("cache_creation_tokens", 0)
    ):
        return None
    total = (
        (incoming - cached) * rates.input + cached * rates.cached + outgoing * rates.output
    ) / 1_000_000
    return total if math.isfinite(total) else None


async def execute(config: RunInput, settings: Settings) -> dict[str, Any]:
    request = payload(config)
    result = await reasoning.complete(request, settings)
    known = default_rates(config.spec.model)
    rates = Rates(**known) if known else None
    usage = (
        result.response.get("usage") if result.response and result.status != "api_error" else None
    )
    row = {
        **config.model_dump(),
        **asdict(result),
        "request": request,
        "usage": usage,
        "requested_model": config.spec.model,
        "correct": False,
        "cost_usd": cost(usage, rates),
        "rates": rates.model_dump() if rates else None,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if result.status == "completed":
        row.update(day_03.evaluate(result.text, config.task, "direct"))
    return row
