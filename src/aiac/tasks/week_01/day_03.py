"""Неделя 1, день 3: условие ещё не получено."""

from aiac.config import Settings
from aiac.llm.client import ModelResult, generate


async def run(prompt: str, settings: Settings) -> ModelResult:
    # Пока только техническая проверка подключения, не реализация задания.
    return await generate(prompt, settings)
