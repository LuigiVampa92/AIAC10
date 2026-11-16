"""Day 10: three memory strategies, checkpoints and independent branches."""

import inspect
from collections.abc import Callable
from copy import deepcopy
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from aiac.agents.branches import (
    MAX_BRANCHES,
    AttachedWriter,
    Workspace,
    checkpoint,
    current,
    fork_pair,
    select,
)
from aiac.agents.compression import SummaryConversation, active_context, compression_end, messages
from aiac.agents.storage import HistoryError, JsonHistory
from aiac.agents.strategies import (
    FACTS_INSTRUCTIONS,
    STRATEGIES,
    TITLES,
    Strategy,
    StrategyConversation,
    facts_context,
    facts_prompt,
    parse_facts,
    strategy_context,
    trim_memory,
    window_context,
)
from aiac.config import ROOT, Settings
from aiac.llm.client import ModelError, ModelResult
from aiac.tasks.week_02 import day_03 as day08
from aiac.tasks.week_02 import day_04 as day09

STORE_ROOT = ROOT / "data" / "week_02" / "day_05"


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: Strategy = "summary"
    keep_last: int = Field(default=6, ge=2, le=20, multiple_of=2, strict=True)
    compress_every: int = Field(default=10, ge=2, le=40, multiple_of=2, strict=True)


class BranchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")


class CheckpointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Checkpoint", min_length=1, max_length=60)


class ForkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checkpoint_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class Comparison(SummaryConversation):
    states: dict[str, StrategyConversation] = Field(default_factory=dict)
    phase: str = "dialogue"
    index: int = 0
    replay_index: int = 0
    recall_index: int = 0
    operations: list[dict[str, Any]] = Field(default_factory=list)
    results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    scenario_version: str = ""


def workspace_store() -> JsonHistory:
    return JsonHistory(STORE_ROOT, state_type=Workspace)


def comparison_store() -> JsonHistory:
    return JsonHistory(STORE_ROOT / "comparison", state_type=Comparison)


def load_workspace(source: JsonHistory) -> Workspace:
    state = source.load()
    assert isinstance(state, Workspace)
    current(state)
    return state


def load_comparison(source: JsonHistory) -> Comparison:
    state = source.load()
    assert isinstance(state, Comparison)
    return state


def public(state: Workspace) -> dict[str, Any]:
    branch = current(state)
    return state.model_dump() | {
        "current": branch.model_dump(),
        "active_context": strategy_context(branch),
        "working_messages": len(messages(branch)),
        "titles": TITLES,
    }


def read() -> dict[str, Any]:
    return public(load_workspace(workspace_store()))


def archive(source: JsonHistory) -> None:
    if source.path("conversation.json").exists():
        old = source.load()
        source.save(old, f"archive-{old.id}.json")


def new(config: Options) -> dict[str, Any]:
    source = workspace_store()
    with source.lock():
        archive(source)
        state = Workspace(branches={"main": StrategyConversation(**config.model_dump())})
        source.save(state)
        return public(state)


def change_strategy(config: Options) -> dict[str, Any]:
    source = workspace_store()
    with source.lock():
        state = load_workspace(source)
        old = current(state)
        if old.status not in ("empty", "completed"):
            raise HistoryError(
                "Смените ветку или начните новое пространство после незавершённого хода."
            )
        if all(getattr(old, k) == v for k, v in config.model_dump().items()):
            return public(state)
        if len(state.branches) >= MAX_BRANCHES:
            raise HistoryError("Достигнут лимит 8 веток. Создайте новое пространство.")
        branch = old.model_copy(deep=True)
        branch.id = uuid4().hex
        branch.parent_branch = state.active_branch
        branch.checkpoint_id = None
        branch.name = TITLES[config.strategy] + f" · {len(state.branches) + 1}"
        branch.history = window_context(old)
        branch.strategy = config.strategy
        branch.keep_last, branch.compress_every = config.keep_last, config.compress_every
        branch.history = window_context(branch)
        branch.facts, branch.facts_log = {}, []
        branch.summary, branch.summarized_messages, branch.compression_log = "", 0, []
        branch.last_operation = {}
        state.branches[branch.id] = branch
        state.active_branch = branch.id
        source.save(state)
        return public(state)


def switch(config: BranchInput) -> dict[str, Any]:
    source = workspace_store()
    with source.lock():
        state = load_workspace(source)
        select(state, config.branch_id)
        source.save(state)
        return public(state)


def save_checkpoint(config: CheckpointInput) -> dict[str, Any]:
    source = workspace_store()
    with source.lock():
        state = load_workspace(source)
        checkpoint(state, config.name)
        source.save(state)
        return public(state)


def fork(config: ForkInput) -> dict[str, Any]:
    source = workspace_store()
    with source.lock():
        state = load_workspace(source)
        fork_pair(state, config.checkpoint_id)
        source.save(state)
        return public(state)


async def update_facts(
    state: StrategyConversation,
    writer: AttachedWriter,
    settings: Settings,
    prompt: str,
    remaining: float,
) -> dict[str, Any]:
    state.stage = "facts"
    writer.save(state)
    previous = deepcopy(state.facts)
    result, data = await day09.operation(
        state,
        writer,
        settings,
        kind="facts",
        prompt=facts_prompt(state, prompt),
        history=[],
        remaining=remaining,
        instructions=FACTS_INSTRUCTIONS,
    )
    if result.status == "completed":
        try:
            updated = parse_facts(result.text)
        except (ValueError, RecursionError):
            data["status"], data["error"] = (
                "invalid_facts",
                "Модель вернула некорректный JSON facts.",
            )
            state.status = "invalid_facts"
        else:
            state.facts = updated
    else:
        state.status = result.status
    state.facts_log.append(
        {
            "user_message": prompt,
            "previous_facts": previous,
            "facts": deepcopy(state.facts),
            "operation": data,
        }
    )
    writer.save(state)
    return data


async def maintain(
    state: StrategyConversation,
    writer: AttachedWriter,
    settings: Settings,
    prompt: str,
    remaining: float,
) -> dict[str, Any] | None:
    if state.strategy == "facts":
        return await update_facts(state, writer, settings, prompt, remaining)
    if state.strategy == "summary":
        return await day09.compress(state, writer, settings, remaining=remaining)
    return None


async def execute(config: day08.ChatInput, settings: Settings) -> dict[str, Any]:
    if not config.prompt.strip():
        raise ValueError("Введите непустое сообщение.")
    source = workspace_store()
    with source.lock():
        workspace = load_workspace(source)
        state = current(workspace)
        day09.guard(state, settings)
        state.model, state.status = settings.aiac_llm_model, "running"
        state.pending_prompt = config.prompt
        writer = AttachedWriter(source, workspace, state)
        writer.save(state)
        operations: list[dict[str, Any]] = []
        support = await maintain(state, writer, settings, config.prompt, day08.MAX_COST_USD)
        if support:
            operations.append(support)
        if state.status == "running":
            state.stage = "answer"
            context = strategy_context(state)
            old_history = deepcopy(state.history)
            result, data = await day09.operation(
                state,
                writer,
                settings,
                kind="answer",
                prompt=config.prompt,
                history=context,
                remaining=day08.MAX_COST_USD - day09.resources(operations)["reserved_usd"],
                reference=messages(state)
                if state.strategy == "summary" and state.summary
                else None,
            )
            operations.append(data)
            state.history = old_history + result.history[len(context) :]
            trim_memory(state)
            state.status = result.status
            state.pending_prompt = ""
            turn = data | {"strategy": state.strategy}
        else:
            turn = {
                "prompt": config.prompt,
                "status": state.status,
                "error": support.get("error") if support else None,
                "strategy": state.strategy,
            }
        turn.update(operations=operations, resources=day09.resources(operations))
        state.turns.append(turn)
        state.stage = "finished"
        writer.save(state)
        return public(workspace)


def complexity() -> dict[str, Any]:
    implementations: dict[str, list[Callable[..., Any]]] = {
        "window": [window_context],
        "facts": [window_context, facts_context, facts_prompt, parse_facts, update_facts],
        "summary": [compression_end, active_context, day09.compress],
    }
    rules = {
        "window": "Нет поддерживающих вызовов модели.",
        "facts": "Обновление после каждого сообщения пользователя, включая проверочный вопрос.",
        "summary": "Вызов при пороге сжатия; в сравнении также финальное сжатие хвоста.",
    }
    return {
        key: {
            "nonblank_lines": sum(
                1
                for function in functions
                for line in inspect.getsource(function).splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ),
            "functions": [function.__name__ for function in functions],
            "maintenance": rules[key],
        }
        for key, functions in implementations.items()
    }


def recommendations(state: Comparison) -> list[str]:
    notes = [
        "Sliding Window подходит короткому локальному обсуждению: "
        "нет расходов на обновление памяти, ранние сведения могут исчезнуть.",
        "Sticky Facts подходит договорённостям и ограничениям: память явная, "
        "но обновления стоят токенов и могут ошибаться.",
        "Summary подходит длинному повествованию: сохраняет связность в сжатом виде, "
        "но может потерять детали и требует обновлений.",
    ]
    if state.phase == "finished":
        values = [r["evaluation"]["remembered"] for r in state.results.values()]
        models = {r["model"] for r in state.results.values()}
        if len(models) != 1:
            notes.append(
                "Фактические модели различаются: прямой итоговый рейтинг не рассчитывается."
            )
        elif all(type(v) is int for v in values):
            best = max(values)
            winners = [
                TITLES[k] for k, r in state.results.items() if r["evaluation"]["remembered"] == best
            ]
            notes.append(
                f"В этом прогоне больше всего деталей ({best}/8) сохранили: {', '.join(winners)}."
            )
        else:
            notes.append("Есть невалидные проверочные ответы: полного рейтинга памяти нет.")
        costs = {k: r["resources"]["cost_usd"] for k, r in state.results.items()}
        if all(type(v) in (int, float) for v in costs.values()):
            winner = min(costs, key=lambda k: costs[k])
            notes.append(
                f"В этом прогоне ниже расчётный расход поддержки и проверки: {TITLES[winner]}."
            )
    return notes


def comparison_public(state: Comparison) -> dict[str, Any]:
    return state.model_dump() | {
        "resources": day09.resources(state.operations),
        "complexity": complexity(),
        "recommendations": recommendations(state),
    }


def comparison_read() -> dict[str, Any]:
    return comparison_public(load_comparison(comparison_store()))


def comparison_start(config: Options) -> dict[str, Any]:
    source = comparison_store()
    with source.lock():
        archive(source)
        state = Comparison(
            keep_last=config.keep_last,
            compress_every=config.compress_every,
            states={
                key: StrategyConversation(
                    strategy=key,
                    name=TITLES[key],
                    keep_last=config.keep_last,
                    compress_every=config.compress_every,
                )
                for key in STRATEGIES
            },
            scenario_version=day09.scenario()["version"],
        )
        source.save(state)
        return comparison_public(state)


def remaining(state: Comparison) -> float:
    return float(day08.MAX_COST_USD - day09.resources(state.operations)["reserved_usd"])


def record(state: Comparison, data: dict[str, Any], strategy: str) -> None:
    state.operations.append(data | {"strategy": strategy})


async def comparison_step(settings: Settings) -> dict[str, Any]:
    source = comparison_store()
    with source.lock():
        state = load_comparison(source)
        day09.guard(state, settings)
        if not state.states or state.phase == "finished":
            return comparison_public(state)
        if state.scenario_version != day09.scenario()["version"]:
            raise HistoryError("Сценарий изменился. Начните новое сравнение.")
        state.model, state.status = settings.aiac_llm_model, "running"
        source.save(state)
        if state.phase == "dialogue":
            prompt = day09.scenario()["user_messages"][state.index]
            context = deepcopy(messages(state))
            archive_history = deepcopy(state.history)
            result, data = await day09.operation(
                state,
                source,
                settings,
                kind="dialogue",
                prompt=prompt,
                history=context,
                remaining=remaining(state),
            )
            record(state, data, "common")
            state.history = archive_history + result.history[len(context) :]
            state.status = result.status
            if result.status == "completed":
                state.index += 1
                if state.index == 12:
                    state.phase = "replay"
        elif state.phase == "replay":
            pair = deepcopy(messages(state)[state.replay_index * 2 : state.replay_index * 2 + 2])
            prompt = pair[0]["content"]
            for key in STRATEGIES:
                branch = state.states[key]
                branch.model, branch.status = settings.aiac_llm_model, "running"
                writer = AttachedWriter(source, state, branch)
                support = await maintain(branch, writer, settings, prompt, remaining(state))
                if support:
                    record(state, support, key)
                if branch.status != "running":
                    state.status = branch.status
                    break
                branch.history.extend(deepcopy(pair))
                trim_memory(branch)
                branch.status = "completed"
            if state.status == "running":
                state.replay_index += 1
                state.status = "completed"
                if state.replay_index == 12:
                    state.phase = "final_summary"
        elif state.phase == "final_summary":
            branch = state.states["summary"]
            branch.status = "running"
            summary_data = await day09.compress(
                branch,
                AttachedWriter(source, state, branch),
                settings,
                remaining=remaining(state),
                force=True,
            )
            if summary_data:
                record(state, summary_data, "summary")
            state.status = "completed" if branch.status == "running" else branch.status
            branch.status = state.status
            if state.status == "completed":
                state.phase = "recall_facts_update"
        elif state.phase == "recall_facts_update":
            branch = state.states["facts"]
            branch.status = "running"
            data = await update_facts(
                branch,
                AttachedWriter(source, state, branch),
                settings,
                day09.recall_prompt(),
                remaining(state),
            )
            record(state, data, "facts")
            state.status = "completed" if branch.status == "running" else branch.status
            branch.status = state.status
            if state.status == "completed":
                state.phase = "recall"
        elif state.phase == "recall":
            key = STRATEGIES[state.recall_index]
            branch = state.states[key]
            result, data = await day09.operation(
                branch,
                AttachedWriter(source, state, branch),
                settings,
                kind="recall",
                prompt=day09.recall_prompt(),
                history=strategy_context(branch),
                remaining=remaining(state),
                reference=messages(state),
            )
            record(state, data, key)
            state.results[key] = data | {
                "evaluation": day09.evaluate(result.text),
                "resources": day09.resources(
                    [op for op in state.operations if op["strategy"] == key]
                ),
                "maintenance_calls": sum(
                    op["api_calls"]
                    for op in state.operations
                    if op["strategy"] == key and op["kind"] != "recall"
                ),
            }
            state.status = result.status
            if result.status == "completed":
                state.recall_index += 1
                if state.recall_index == len(STRATEGIES):
                    state.phase = "finished"
        source.save(state)
        return comparison_public(state)


async def run(prompt: str, settings: Settings) -> ModelResult:
    raise ModelError("Используйте диалог со стратегиями и ветками.")
