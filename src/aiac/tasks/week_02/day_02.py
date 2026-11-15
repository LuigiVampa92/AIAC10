"""Day 7: continue a tool-calling agent from durable local history."""

from typing import Any

from aiac.agents.simple import AgentResult, SimpleAgent
from aiac.agents.storage import Conversation, HistoryError, JsonHistory
from aiac.config import ROOT, Settings
from aiac.llm.client import ModelError, ModelResult
from aiac.tasks.week_02.day_01 import AgentInput

STORE_ROOT = ROOT / "data" / "week_02" / "day_02"


def snapshot(state: Conversation) -> dict[str, Any]:
    return state.model_dump()


def read() -> dict[str, Any]:
    return snapshot(JsonHistory(STORE_ROOT).load())


def new() -> dict[str, Any]:
    store = JsonHistory(STORE_ROOT)
    with store.lock():
        return snapshot(store.new())


def turn_summary(result: AgentResult, prompt: str) -> dict[str, Any]:
    usage: dict[str, int | None] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        values = [(step.get("usage") or {}).get(key) for step in result.steps]
        usage[key] = (
            sum(v for v in values if isinstance(v, int))
            if values and all(type(v) is int for v in values)
            else None
        )
    return {
        "prompt": prompt,
        "text": result.text,
        "status": result.status,
        "error": result.error,
        "model": result.model,
        "seconds": result.seconds,
        "usage": usage,
        "api_calls": len(result.steps),
        "tools": [tool for step in result.steps for tool in step["tools"]],
    }


async def execute(config: AgentInput, settings: Settings) -> dict[str, Any]:
    if not config.prompt.strip():
        raise ValueError("Введите непустое сообщение.")
    # Reject configuration problems before creating/changing durable history.
    if settings.aiac_llm_mode != "live" or settings.aiac_llm_provider != "openai":
        raise ModelError("Для агента нужны live и провайдер openai.")
    if not settings.openai_api_key or not settings.aiac_llm_model.strip():
        raise ModelError("Настройте OPENAI_API_KEY и AIAC_LLM_MODEL.")
    store = JsonHistory(STORE_ROOT)
    with store.lock():
        state = store.load()
        if state.status not in ("empty", "completed"):
            raise HistoryError(
                "Последний ход не завершён. Автоповтора нет. "
                "Начните новый диалог; старый сохранится."
            )
        if state.model and state.model != settings.aiac_llm_model:
            raise HistoryError("Модель изменилась. Начните новый диалог для новой модели.")
        state.model = settings.aiac_llm_model
        state.tools = list(dict.fromkeys(config.tools))
        previous = state.history
        state.turns.append({})

        def checkpoint(result: AgentResult) -> None:
            state.history = result.history
            state.status = "running"
            state.turns[-1] = turn_summary(result, config.prompt)
            store.save(state)

        result = await SimpleAgent(settings, list(config.tools)).run(
            config.prompt, history=previous, on_update=checkpoint
        )
        state.status = result.status
        state.history = result.history
        state.turns[-1] = turn_summary(result, config.prompt)
        store.save(state)
        return snapshot(state)


async def run(prompt: str, settings: Settings) -> ModelResult:
    raise ModelError("Используйте форму диалога с сохранением контекста.")
