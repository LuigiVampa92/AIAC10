"""Неделя 1, день 1: первый запрос к LLM через общую API-границу."""

from aiac.config import Settings
from aiac.llm.client import ModelResult, generate


async def run(prompt: str, settings: Settings) -> ModelResult:
    return await generate(prompt, settings)
