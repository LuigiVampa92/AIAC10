import json
from dataclasses import dataclass
from typing import TypedDict

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI

from aiac.config import Settings


class ModelError(Exception):
    """Safe user-facing error."""

    def __init__(self, message: str, *, request_json: str = "") -> None:
        super().__init__(message)
        self.request_json = request_json


class RequestPayload(TypedDict):
    model: str
    input: str
    max_output_tokens: int


def provider_error_message(error: APIError) -> str:
    if isinstance(error, APITimeoutError):
        return "OpenAI API не ответил вовремя. Попробуйте отправить запрос позже."
    if isinstance(error, APIConnectionError):
        return "Не удалось подключиться к OpenAI API. Проверьте интернет, VPN и прокси."
    if isinstance(error, APIStatusError):
        if error.code in {"credit_balance_exhausted", "insufficient_quota"}:
            return (
                "Недостаточно средств или доступной квоты OpenAI API. "
                "Проверьте баланс и биллинг API-проекта. Подписка ChatGPT их не заменяет."
            )
        if error.status_code == 401:
            return "OpenAI API отклонил ключ. Проверьте OPENAI_API_KEY и его срок действия."
        if error.status_code == 403:
            return "OpenAI API запретил запрос. Проверьте разрешения ключа и доступ API-проекта."
        if error.status_code == 404:
            return "Модель или ресурс OpenAI API не найдены. Проверьте AIAC_LLM_MODEL и доступ."
        if error.status_code == 429:
            return "Достигнут лимит запросов OpenAI API. Подождите и проверьте лимиты API-проекта."
        if error.status_code == 400:
            return "OpenAI API отклонил параметры запроса. Проверьте поддержку параметров моделью."
    return "OpenAI API недоступен. Попробуйте позже или проверьте состояние сервиса."


@dataclass(frozen=True)
class ModelResult:
    text: str
    mode: str
    model: str
    request_json: str = ""
    response_json: str = ""
    provider: str = "openai"


async def generate(prompt: str, settings: Settings) -> ModelResult:
    if settings.aiac_llm_mode == "demo":
        return ModelResult(
            text=f"Демонстрационная заглушка. API не вызывался.\n\nВаш ввод: {prompt}",
            mode="demo",
            model="demo-placeholder",
            provider="none",
        )
    if settings.aiac_llm_provider != "openai":
        raise ModelError("Выбранный AIAC_LLM_PROVIDER пока не поддерживается. Доступен openai.")
    return await generate_openai(prompt, settings)


async def generate_openai(prompt: str, settings: Settings) -> ModelResult:
    """OpenAI adapter; provider-specific credentials never leave this boundary."""
    key = settings.openai_api_key
    if not key or not key.get_secret_value() or not settings.aiac_llm_model.strip():
        raise ModelError("Для режима OpenAI нужны OPENAI_API_KEY и AIAC_LLM_MODEL.")
    payload: RequestPayload = {
        "model": settings.aiac_llm_model,
        "input": prompt,
        "max_output_tokens": 512,
    }
    request_json = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        async with AsyncOpenAI(
            api_key=key.get_secret_value(), timeout=30.0, max_retries=0
        ) as client:
            response = await client.responses.create(**payload)
    except APIError as exc:
        raise ModelError(
            provider_error_message(exc),
            request_json=request_json,
        ) from exc
    return ModelResult(
        text=response.output_text,
        mode="live",
        model=response.model,
        request_json=request_json,
        response_json=response.model_dump_json(indent=2),
    )
