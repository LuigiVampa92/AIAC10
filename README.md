# AIAC

Лаборатория челленджа: **7 недель × 5 дней**. Код создаётся агентами через OpenCode.
Все 35 страниц подготовлены, но условия заданий пока не получены.

## Быстрый старт

Python 3.12 и uv. Если uv отсутствует, установить его локально, без глобальных изменений:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install uv
```

```sh
UV_CACHE_DIR=.cache/uv UV_PYTHON_DOWNLOADS=never .venv/bin/uv sync --inexact --frozen
.venv/bin/python -m uvicorn aiac.web.app:app --reload --host 127.0.0.1
```

Открыть http://127.0.0.1:8000. Активация venv не обязательна.
`--inexact` сохраняет локальный bootstrap uv, зависимости приложения закреплены в uv.lock.
В чистом окружении с глобальным uv можно использовать `uv sync --frozen`.

## Модели и оплата

По умолчанию работает **demo**, без API и расходов. Настройки читаются из окружения
или `.env` в корне. Пример — `.env.example`, настоящий `.env` в Git не попадает.

Реальный режим включается пользователем: `AIAC_LLM_MODE=openai`, отдельный
`OPENAI_API_KEY`, доступный API-аккаунту `AIAC_OPENAI_MODEL`.
**ChatGPT Plus применяется для работы в OpenCode, но не оплачивает OpenAI API.**
Агенты не должны самостоятельно включать платный режим или проверять его живыми вызовами.
Сервер только локальный; не публиковать без дополнительных мер защиты.

## Проверки

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pytest
```

Тесты не используют реальный API. OpenAI-интеграция проверяется подменой клиента.

## Навигация

- [Правила агентов](AGENTS.md), [условия](docs/challenge.md), [архитектура](docs/architecture.md).
- `docs/tasks/week_XX/day_YY.md` — условие и статус дня, источник описания в браузере.
- `src/aiac/tasks/week_XX/day_YY.py` — реализация дня.
- `src/aiac/web/`, `src/aiac/llm/` — общая оболочка и интеграция моделей.
- `demo/checklist.md` — памятка для записи видео.

Git инициализирован без remote и коммитов. Индекс, commit, push, remote и теги
пользователь контролирует самостоятельно. Конфигурация OpenCode рассчитана на V2;
проект не меняет глобальные настройки и выбранную пользователем модель.
