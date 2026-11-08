"""One bounded Chat Completions call; shared provider boundary for experiments."""

import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast

from openai import APIError, APIStatusError, AsyncOpenAI
from openai.types.chat.completion_create_params import CompletionCreateParamsNonStreaming

from aiac.config import Settings
from aiac.llm.client import ModelError, provider_error_message


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str
    finish_reason: str
    refusal: bool
    seconds: float
    usage: dict[str, Any] | None
    response: dict[str, Any]


class ChatError(ModelError):
    def __init__(
        self,
        message: str,
        *,
        request_json: str,
        stop_batch: bool,
        response: dict[str, Any] | None,
    ) -> None:
        super().__init__(message, request_json=request_json)
        self.stop_batch = stop_batch
        self.response = response


def safe_error_response(error: APIError, api_key: str) -> dict[str, Any] | None:
    """Expose only selected HTTP error fields; never exception traces or headers."""
    if not isinstance(error, APIStatusError):
        return None
    body = error.body
    raw = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    safe: dict[str, str | None] = {}
    for field in ("message", "type", "param", "code"):
        value = raw.get(field)
        if value is None:
            safe[field] = None
        elif isinstance(value, str):
            value = value.replace(api_key, "[REDACTED]")
            value = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", value)
            value = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
            safe[field] = value[:2000]
    return {"http_status": error.status_code, "error": safe}


async def complete(payload: dict[str, Any], settings: Settings) -> ChatResult:
    if settings.aiac_llm_mode != "live" or settings.aiac_llm_provider != "openai":
        raise ModelError("Для эксперимента нужны live и провайдер openai.")
    key = settings.openai_api_key
    if not key or not key.get_secret_value().strip() or not settings.aiac_llm_model.strip():
        raise ModelError("Настройте OPENAI_API_KEY и AIAC_LLM_MODEL.")
    started = perf_counter()
    try:
        async with AsyncOpenAI(
            api_key=key.get_secret_value(), timeout=30.0, max_retries=0
        ) as client:
            response = await client.chat.completions.create(
                **cast(CompletionCreateParamsNonStreaming, payload)
            )
    except APIError as exc:
        diagnostic = safe_error_response(exc, key.get_secret_value())
        message = provider_error_message(exc)
        if diagnostic and diagnostic["http_status"] == 400:
            error_fields = diagnostic["error"]
            if error_fields.get("param") == "stop":
                message = (
                    "Выбранная модель не поддерживает параметр stop. "
                    "Запрос отклонён до получения ответа модели."
                    if error_fields.get("code") == "unsupported_parameter"
                    else "OpenAI API отклонил параметр stop. Подробности — в ответе API."
                )
        # A rejected parameter blocks this variant; connection/auth/quota errors stop the batch.
        raise ChatError(
            message,
            request_json=json.dumps(payload, ensure_ascii=False, indent=2),
            stop_batch=getattr(exc, "status_code", None) != 400,
            response=diagnostic,
        ) from exc
    elapsed = round(perf_counter() - started, 3)
    raw = response.model_dump(mode="json")
    if not response.choices:
        return ChatResult("", response.model, "empty", False, elapsed, raw.get("usage"), raw)
    choice = response.choices[0]
    return ChatResult(
        choice.message.content or "",
        response.model,
        choice.finish_reason,
        bool(choice.message.refusal),
        elapsed,
        raw.get("usage"),
        raw,
    )
