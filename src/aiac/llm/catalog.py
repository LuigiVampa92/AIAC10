"""Read-only model discovery; model IDs do not describe endpoint capabilities or prices."""

import re

from openai import APIError, AsyncOpenAI

from aiac.config import Settings
from aiac.llm.client import ModelError, provider_error_message


def is_text_model(model_id: str) -> bool:
    """Conservative ID filter: models.list has no modality metadata.

    Include text generation families and their snapshots/fine-tunes, excluding
    specialized variants. This is not a promise of Responses API compatibility.
    """
    base = model_id.casefold()
    if base.startswith("ft:"):
        base = base.split(":", 2)[1]
    if any(
        part in base
        for part in (
            "audio",
            "realtime",
            "image",
            "transcrib",
            "tts",
            "embedding",
            "moderation",
            "search",
            "whisper",
            "sora",
            "video",
        )
    ):
        return False
    return bool(re.match(r"^(?:gpt-\d|chatgpt-\d|o[134](?:-|$)|gpt-oss(?:-|$))", base))


async def list_models(settings: Settings) -> list[str]:
    if settings.aiac_llm_mode != "live" or settings.aiac_llm_provider != "openai":
        raise ModelError("Для загрузки моделей нужны live и провайдер openai.")
    key = settings.openai_api_key
    if not key or not key.get_secret_value().strip():
        raise ModelError("Настройте OPENAI_API_KEY.")
    try:
        async with AsyncOpenAI(
            api_key=key.get_secret_value(), timeout=30.0, max_retries=0
        ) as client:
            page = await client.models.list()
            return sorted({model.id async for model in page if is_text_model(model.id)})
    except APIError as exc:
        raise ModelError(provider_error_message(exc)) from exc
