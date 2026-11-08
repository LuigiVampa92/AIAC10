"""Generate missing day placeholders only; never overwrite existing work."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MODULE = '''"""Неделя {week}, день {day}: условие ещё не получено."""

from aiac.config import Settings
from aiac.llm.client import ModelResult, generate


async def run(prompt: str, settings: Settings) -> ModelResult:
    # Пока только техническая проверка подключения, не реализация задания.
    return await generate(prompt, settings)
'''

DOCUMENT = """# Неделя {week} · День {day}

**Статус:** ожидает условия.

## Оригинальное условие
Пока не предоставлено. Не придумывать задание.

## Критерии приёмки
Будут определены после получения условия.

## Решение и проверка
Пока доступна только техническая форма проверки подключения. Задание не выполнено.

## Демонстрация
Сценарий, commit и ссылка на видео будут добавлены после реализации.
"""


def create_missing(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def main() -> None:
    for week in range(1, 8):
        package = ROOT / "src" / "aiac" / "tasks" / f"week_{week:02}"
        create_missing(package / "__init__.py", '"""Week task modules."""\n')
        for day in range(1, 6):
            create_missing(package / f"day_{day:02}.py", MODULE.format(week=week, day=day))
            create_missing(
                ROOT / "docs" / "tasks" / f"week_{week:02}" / f"day_{day:02}.md",
                DOCUMENT.format(week=week, day=day),
            )


if __name__ == "__main__":
    main()
