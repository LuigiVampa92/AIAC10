from dataclasses import dataclass

from openai import APIError, AsyncOpenAI

from aiac.config import Settings


class ModelError(Exception):
    """Safe user-facing error."""


@dataclass(frozen=True)
class ModelResult:
    text: str
    mode: str
    model: str


async def generate(prompt: str, settings: Settings) -> ModelResult:
    if settings.aiac_llm_mode == "demo":
        return ModelResult(
            text=f"Демонстрационная заглушка. API не вызывался.\n\nВаш ввод: {prompt}",
            mode="demo",
            model="demo-placeholder",
        )
    key = settings.openai_api_key
    if not key or not key.get_secret_value() or not settings.aiac_openai_model.strip():
        raise ModelError("Для режима OpenAI нужны OPENAI_API_KEY и AIAC_OPENAI_MODEL.")
    try:
        async with AsyncOpenAI(
            api_key=key.get_secret_value(), timeout=30.0, max_retries=0
        ) as client:
            response = await client.responses.create(
                model=settings.aiac_openai_model,
                input=prompt,
                max_output_tokens=512,
            )
    except APIError as exc:
        raise ModelError("OpenAI API недоступен. Проверьте модель, биллинг и подключение.") from exc
    return ModelResult(text=response.output_text, mode="openai", model=response.model)
