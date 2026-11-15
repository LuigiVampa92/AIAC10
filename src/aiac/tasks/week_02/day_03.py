"""Day 8: instrumented agent, durable manual chat with token measurements."""

from copy import deepcopy
from time import perf_counter
from typing import Any

from pydantic import Field

from aiac.agents.simple import AgentResult, SimpleAgent
from aiac.agents.storage import JsonHistory
from aiac.config import ROOT, Settings
from aiac.llm import tokens
from aiac.llm.client import ModelError, ModelResult
from aiac.tasks.week_02 import day_02
from aiac.tasks.week_02.day_01 import AgentInput, ToolName

STORE_ROOT = ROOT / "data" / "week_02" / "day_03"
MAX_EXPERIMENT_CHARS = 12_000_000
MAX_MESSAGE_CHARS = 400_000
MAX_COST_USD = 0.5


class ChatInput(AgentInput):
    prompt: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    tools: list[ToolName] = Field(default_factory=list, max_length=0)


class TokenMeter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.budget = MAX_COST_USD
        self.calls = 0
        self.reserved_cost = 0.0
        self.current: int | None = None
        self.history: int | None = None
        self.measurements: list[dict[str, Any]] = []
        self.blocked: str | None = None

    async def count(self, payload: dict[str, Any]) -> int:
        self.calls += 1
        try:
            return await tokens.count(payload, self.settings)
        except ModelError:
            self.blocked = "count_api_error"
            raise

    async def before_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["truncation"] = "disabled"
        # Day 8 is a plain conversation, with no schemas or tool-selection parameters.
        for key in ("tools", "tool_choice", "parallel_tool_calls"):
            payload.pop(key, None)
        started = perf_counter()
        if not self.measurements:
            # Isolated message counts include message framing; not additive parts of full input.
            self.current = await self.count(
                {
                    "model": payload["model"],
                    "input": [deepcopy(payload["input"][-1])],
                }
            )
            previous = deepcopy(payload["input"][:-1])
            self.history = (
                await self.count({"model": payload["model"], "input": previous}) if previous else 0
            )
        full = await self.count(payload)
        info = {
            "current_message_tokens": self.current,
            "history_before_turn_tokens": self.history,
            "full_input_tokens": full,
            "count_seconds": round(perf_counter() - started, 3),
            "method": "responses.input_tokens.count",
        }
        self.measurements.append(info)
        p = tokens.profile(payload["model"])
        bound = tokens.upper_cost(full, payload["max_output_tokens"], payload["model"])
        info["cost_upper_usd"] = bound
        if bound is None or self.reserved_cost + bound > self.budget:
            self.blocked = "cost_guard"
            if bound is None:
                raise ModelError("Нет проверенного тарифа модели: оценка стоимости недоступна.")
            raise ModelError(
                f"Оценка с резервом на ответ: ${self.reserved_cost + bound:.6f}; "
                f"разрешено ${self.budget:.6f}. Лимит фиксирован. "
                "Начните новый диалог или уменьшите объём текста."
            )
        if p:
            info["context_window"] = p["context_window"]
            info["context_percent"] = round(100 * full / p["context_window"], 2)
        self.reserved_cost += bound
        return info

    def summary(self, result: AgentResult, prompt: str) -> dict[str, Any]:
        data = day_02.turn_summary(result, prompt)
        attempted = [s for s in result.steps if s.get("generation_started")]
        costs = [tokens.cost(s.get("usage"), s.get("model", result.model)) for s in attempted]
        known = [c for c in costs if c is not None]
        data.update(
            api_calls=len(attempted),
            counting_calls=self.calls,
            measurements=deepcopy(self.measurements),
            current_message_tokens=self.current,
            history_before_turn_tokens=self.history,
            cost_usd=sum(known) if costs and len(known) == len(costs) else None,
            known_cost_usd=sum(known),
            cost_complete=bool(costs) and len(known) == len(costs),
            cost_upper_usd=self.reserved_cost,
            profile=tokens.profile(self.settings.aiac_llm_model),
            outcome=self.blocked or result.status,
        )
        # Preserve safe provider diagnostics without echoing the huge probe request.
        errors = [s["response"] for s in attempted if s.get("status") == "api_error"]
        if errors:
            data["provider_error"] = errors[-1]
            error = (errors[-1] or {}).get("error", {})
            if error.get("code") in ("context_length_exceeded", "context_window_exceeded"):
                data["outcome"] = "model_context_overflow"
        return data


def describe(state: dict[str, Any], settings: Settings) -> dict[str, Any]:
    costs = [t.get("cost_usd") for t in state["turns"]]
    known = [c for c in costs if isinstance(c, (int, float))]
    state["total_cost_usd"] = sum(known) if costs and len(known) == len(costs) else None
    state["known_cost_usd"] = sum(known)
    state["profile"] = tokens.profile(settings.aiac_llm_model)
    state["count_source"] = tokens.COUNT_SOURCE
    return state


def read(settings: Settings) -> dict[str, Any]:
    return describe(JsonHistory(STORE_ROOT).load().model_dump(), settings)


def new(settings: Settings) -> dict[str, Any]:
    store = JsonHistory(STORE_ROOT)
    with store.lock():
        return describe(store.new().model_dump(), settings)


def make_agent(settings: Settings, meter: TokenMeter) -> SimpleAgent:
    agent = SimpleAgent(
        settings,
        [],
        before_call=meter.before_call,
        max_context_chars=MAX_EXPERIMENT_CHARS,
        max_prompt_chars=MAX_MESSAGE_CHARS,
    )
    agent.instructions = (
        "Ты собеседник. Отвечай на сообщения пользователя и учитывай всю историю диалога. "
        "Если пользователь просит сохранить информацию, используй её в следующих ответах. "
        "Отвечай кратко, если пользователь не просит подробностей."
    )
    return agent


async def execute(config: ChatInput, settings: Settings) -> dict[str, Any]:
    meter = TokenMeter(settings)
    state = await day_02.execute(
        config,
        settings,
        directory=STORE_ROOT,
        agent=make_agent(settings, meter),
        summarize=meter.summary,
    )
    return describe(state, settings)


async def run(prompt: str, settings: Settings) -> ModelResult:
    raise ModelError("Используйте форму эксперимента с токенами.")
