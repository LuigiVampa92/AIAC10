"""Provider-side input counting and documented Luna resource estimates."""

from typing import Any, cast

from openai import APIError, AsyncOpenAI
from openai.types.responses.input_token_count_params import InputTokenCountParams

from aiac.config import Settings
from aiac.llm.client import ModelError, provider_error_message

SOURCE = "https://developers.openai.com/api/docs/models/gpt-6-luna"
COUNT_SOURCE = "https://developers.openai.com/api/docs/guides/token-counting"
CHECKED = "2026-11-15"


def profile(model: str) -> dict[str, Any] | None:
    # Exact ID only: do not guess limits/prices for snapshots or other families.
    if model != "gpt-6-luna":
        return None
    return {
        "model": model,
        "context_window": 1_050_000,
        "input": 0.10,
        "cached": 0.01,
        "cache_write": 0.125,
        "output": 0.50,
        "long_threshold": 272_000,
        "source": SOURCE,
        "checked": CHECKED,
    }


async def count(payload: dict[str, Any], settings: Settings) -> int:
    key = settings.openai_api_key
    if settings.aiac_llm_mode != "live" or settings.aiac_llm_provider != "openai":
        raise ModelError("Подсчёт токенов требует live и провайдер openai.")
    if not key or not key.get_secret_value().strip():
        raise ModelError("Настройте OPENAI_API_KEY.")
    allowed = {
        "model",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "text",
        "truncation",
    }
    request = {k: v for k, v in payload.items() if k in allowed}
    try:
        async with AsyncOpenAI(api_key=key.get_secret_value(), timeout=30, max_retries=0) as client:
            value = await client.responses.input_tokens.count(
                **cast(InputTokenCountParams, request)
            )
    except APIError as exc:
        raise ModelError("Подсчёт токенов: " + provider_error_message(exc)) from None
    if type(value.input_tokens) is not int or value.input_tokens < 0:
        raise ModelError("API подсчёта не вернул корректное число токенов.")
    return value.input_tokens


def upper_cost(incoming: int, outgoing: int, model: str) -> float | None:
    p = profile(model)
    if p is None:
        return None
    long = incoming > p["long_threshold"]
    # Conservative text-token estimate including possible cache writes.
    return float(
        (
            incoming * max(p["input"], p["cache_write"]) * (2 if long else 1)
            + outgoing * p["output"] * (1.5 if long else 1)
        )
        / 1_000_000
    )


def cost(usage: dict[str, Any] | None, model: str) -> float | None:
    p = profile(model)
    if not usage or p is None:
        return None
    incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
    detail = usage.get("input_tokens_details")
    if not isinstance(detail, dict):
        return None
    cached, written = detail.get("cached_tokens"), detail.get("cache_write_tokens", 0)
    if any(type(v) is not int or v < 0 for v in (incoming, outgoing, cached, written)):
        return None
    assert isinstance(incoming, int) and isinstance(outgoing, int)
    assert isinstance(cached, int) and isinstance(written, int)
    if cached + written > incoming:
        return None
    if usage.get("cache_creation_input_tokens") or detail.get("cache_creation_tokens"):
        return None
    long = incoming > p["long_threshold"]
    return float(
        (
            (
                (incoming - cached - written) * p["input"]
                + cached * p["cached"]
                + written * p["cache_write"]
            )
            * (2 if long else 1)
            + outgoing * p["output"] * (1.5 if long else 1)
        )
        / 1_000_000
    )
