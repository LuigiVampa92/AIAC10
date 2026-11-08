"""Bounded Responses API boundary for reasoning experiments."""

from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast

from openai import APIError, AsyncOpenAI
from openai.types.responses.response_create_params import ResponseCreateParamsNonStreaming

from aiac.config import Settings
from aiac.llm.chat import safe_error_response
from aiac.llm.client import ModelError, provider_error_message


@dataclass(frozen=True)
class ReasoningResult:
    response: dict[str, Any] | None
    text: str
    model: str
    status: str
    seconds: float
    error: str | None = None


async def complete(payload: dict[str, Any], settings: Settings) -> ReasoningResult:
    if settings.aiac_llm_mode != "live" or settings.aiac_llm_provider != "openai":
        raise ModelError("Для эксперимента нужны live и провайдер openai.")
    key = settings.openai_api_key
    if not key or not key.get_secret_value().strip():
        raise ModelError("Настройте OPENAI_API_KEY.")
    started = perf_counter()
    try:
        async with AsyncOpenAI(
            api_key=key.get_secret_value(), timeout=120.0, max_retries=0
        ) as client:
            response = await client.responses.create(
                **cast(ResponseCreateParamsNonStreaming, payload)
            )
    except APIError as exc:
        return ReasoningResult(
            safe_error_response(exc, key.get_secret_value()),
            "",
            str(payload["model"]),
            "api_error",
            round(perf_counter() - started, 3),
            provider_error_message(exc),
        )
    raw = response.model_dump(mode="json")
    refusal = any(
        part.get("type") == "refusal"
        for item in raw.get("output", [])
        for part in item.get("content", [])
    )
    status = "refusal" if refusal else str(response.status)
    if status == "completed" and not response.output_text.strip():
        status = "empty"
    return ReasoningResult(
        raw,
        response.output_text,
        response.model,
        status,
        round(perf_counter() - started, 3),
    )
