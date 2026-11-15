"""A bounded tool-calling agent, independent of web routes and task UI."""

import asyncio
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from aiac.agents.tools import SCHEMAS, execute_tool
from aiac.config import Settings
from aiac.llm import reasoning
from aiac.llm.client import ModelError

MAX_STEPS = 6
MAX_OUTPUT_TOKENS = 4096
MAX_CONTEXT_CHARS = 60000
TOTAL_TIMEOUT = 120
INSTRUCTIONS = (
    "Ты агент с настраиваемым набором инструментов. Используй только доступные инструменты, "
    "когда они нужны. Для конвертации используй fixed_exchange, для сложения calculator, "
    "для точного времени current_time, если соответствующий инструмент доступен. "
    "Если инструмент недоступен, выполни соответствующую часть запроса самостоятельно. "
    "Не выдумывай результаты вызовов инструментов. "
    "Вызывай инструменты последовательно и используй их результаты. "
    "В JSON-поле timestamp всегда используй строку UTC формата YYYY-MM-DDTHH:mm:ssZ, "
    "без поясняющего текста внутри поля. "
    "Соблюдай запрошенный формат финального ответа."
)


@dataclass
class AgentResult:
    text: str = ""
    status: str = "running"
    model: str = ""
    error: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0


def replay_input(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replay conversation data without response-only item status metadata.

    Keep the raw history for inspection and preserve IDs, call arguments and
    opaque reasoning content. Some API deployments reject status on replayed
    output items, including the null values emitted by SDK model_dump.
    """
    items = deepcopy(history)
    for item in items:
        item.pop("status", None)
    return items


class SimpleAgent:
    def __init__(self, settings: Settings, tools: list[str] | None = None) -> None:
        self.settings = settings
        self.tools = list(
            dict.fromkeys(tools if tools is not None else [s["name"] for s in SCHEMAS])
        )
        if any(name not in {s["name"] for s in SCHEMAS} for name in self.tools):
            raise ValueError("Неизвестный инструмент.")
        self.instructions = INSTRUCTIONS + (
            " Если пользователь спрашивает текущее время, обязательно получи его через "
            "доступный current_time и используй результат локальной функции."
            if "current_time" in self.tools
            else " Инструмент current_time отключён: на вопрос о времени ответь самостоятельно "
            "как языковая модель, без вызова часов. Для этого эксперимента дай приблизительную "
            "оценку времени в формате YYYY-MM-DDTHH:mm:ssZ, даже если точное время неизвестно. "
            "Не возвращай null или фразу о неизвестном времени в timestamp. "
            "Это предположительная метка, а не результат проверки часов."
        )

    async def run(
        self,
        prompt: str,
        *,
        history: list[dict[str, Any]] | None = None,
        on_update: Callable[[AgentResult], None] | None = None,
    ) -> AgentResult:
        if not prompt.strip() or len(prompt) > 4000:
            raise ValueError("Введите непустой запрос до 4000 символов.")
        if self.settings.aiac_llm_mode != "live":
            raise ModelError("Для агента нужен реальный режим live. Demo не выполняет задание.")
        if self.settings.aiac_llm_provider != "openai" or not self.settings.aiac_llm_model.strip():
            raise ModelError("Настройте провайдер openai и AIAC_LLM_MODEL.")
        key = self.settings.openai_api_key
        if not key or not key.get_secret_value().strip():
            raise ModelError("Настройте OPENAI_API_KEY.")
        result = AgentResult(
            model=self.settings.aiac_llm_model,
            history=deepcopy(history or []) + [{"role": "user", "content": prompt}],
        )
        started = perf_counter()
        seen_ids: set[str] = {
            item["call_id"]
            for item in result.history
            if item.get("type") == "function_call" and isinstance(item.get("call_id"), str)
        }
        if on_update:
            on_update(result)
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT):
                for number in range(1, MAX_STEPS + 1):
                    if len(json.dumps(result.history, ensure_ascii=False)) > MAX_CONTEXT_CHARS:
                        result.status, result.error = "limit", "Достигнут предел размера контекста."
                        break
                    payload = {
                        "model": self.settings.aiac_llm_model,
                        "input": replay_input(result.history),
                        "instructions": self.instructions,
                        "tools": deepcopy([s for s in SCHEMAS if s["name"] in self.tools]),
                        "tool_choice": "auto",
                        "parallel_tool_calls": False,
                        "max_output_tokens": MAX_OUTPUT_TOKENS,
                        "store": False,
                        "include": ["reasoning.encrypted_content"],
                    }
                    step: dict[str, Any] = {
                        "number": number,
                        "request": payload,
                        "response": None,
                        "status": "pending",
                        "tools": [],
                    }
                    result.steps.append(step)
                    if on_update:
                        on_update(result)
                    call = await reasoning.complete(payload, self.settings, timeout_seconds=30)
                    step.update(
                        response=call.response,
                        status=call.status,
                        model=call.model,
                        seconds=call.seconds,
                        text=call.text,
                        usage=call.response.get("usage") if call.response else None,
                    )
                    result.model = call.model
                    output = (call.response or {}).get("output", [])
                    result.history.extend(deepcopy(output))
                    if on_update:
                        on_update(result)
                    if call.status != "completed":
                        result.status = call.status
                        result.error = (
                            call.error or "Модель не завершила ответ. Цикл остановлен без повтора."
                        )
                        break
                    tool_calls = [item for item in output if item.get("type") == "function_call"]
                    if not tool_calls:
                        result.text = call.text
                        if call.text.strip() and not any(
                            i.get("type") == "message" for i in output
                        ):
                            result.history.append({"role": "assistant", "content": call.text})
                        result.status = "completed" if call.text.strip() else "empty"
                        if result.status == "empty":
                            result.error = "Модель не вернула финальный текст."
                        break
                    if len(tool_calls) != 1:
                        result.status, result.error = (
                            "protocol_error",
                            "Ожидался один последовательный вызов.",
                        )
                        break
                    if number == MAX_STEPS:
                        result.status, result.error = (
                            "limit",
                            "Достигнут лимит 6 обращений к модели.",
                        )
                        break
                    tool = tool_calls[0]
                    call_id, name, arguments = (
                        tool.get("call_id"),
                        tool.get("name"),
                        tool.get("arguments"),
                    )
                    if (
                        not isinstance(call_id, str)
                        or not call_id
                        or call_id in seen_ids
                        or not isinstance(name, str)
                        or name not in self.tools
                        or not isinstance(arguments, str)
                    ):
                        result.status, result.error = (
                            "protocol_error",
                            "Некорректный вызов инструмента.",
                        )
                        break
                    seen_ids.add(call_id)
                    answer = execute_tool(name, arguments)
                    step["tools"].append(
                        {"call_id": call_id, "name": name, "arguments": arguments, "output": answer}
                    )
                    result.history.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(answer, ensure_ascii=False),
                        }
                    )
                    if on_update:
                        on_update(result)
        except TimeoutError:
            result.status = "timeout"
            result.error = (
                "Превышено общее время 120 с. Исход последнего вызова может быть неизвестен."
            )
            if result.steps and result.steps[-1]["status"] == "pending":
                result.steps[-1]["status"] = "unknown"
        except ModelError as exc:
            result.status, result.error = "configuration_error", str(exc)
            if result.steps and result.steps[-1]["status"] == "pending":
                result.steps[-1]["status"] = "configuration_error"
        result.seconds = round(perf_counter() - started, 3)
        return result
