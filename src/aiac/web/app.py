from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt

from aiac.config import Settings
from aiac.llm.client import ModelError
from aiac.tasks.registry import TASKS, Task

WEB = Path(__file__).parent
app = FastAPI(title="AIAC · 35 заданий")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
templates = Jinja2Templates(directory=WEB / "templates")
markdown = MarkdownIt("commonmark", {"html": False})


def get_task(week: int, day: int) -> Task:
    task = TASKS.get((week, day))
    if task is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    return task


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="index.html", context={"tasks": list(TASKS.values())}
    )


def task_page(
    request: Request,
    task: Task,
    settings: Settings,
    *,
    prompt: str = "",
    result: dict[str, str] | None = None,
    error: str | None = None,
    request_json: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    # Public pages show the original task; implementation reports stay in docs.
    document = task.document.read_text(encoding="utf-8")
    _, separator, original = document.partition("## Оригинальное условие\n")
    description = original.split("\n## ", 1)[0] if separator else "Описание пока не добавлено."
    return templates.TemplateResponse(
        request=request,
        name="task.html",
        context={
            "task": task,
            "description": markdown.render(description),
            "mode": settings.aiac_llm_mode,
            "provider": settings.aiac_llm_provider,
            "model": settings.aiac_llm_model if settings.aiac_llm_mode == "live" else "",
            "prompt": prompt,
            "result": result,
            "error": error,
            "request_json": request_json,
        },
        status_code=status_code,
    )


@app.get("/weeks/{week}/days/{day}", response_class=HTMLResponse)
async def task_detail(request: Request, week: int, day: int) -> HTMLResponse:
    return task_page(request, get_task(week, day), Settings())


@app.post("/weeks/{week}/days/{day}", response_class=HTMLResponse)
async def task_run(
    request: Request,
    week: int,
    day: int,
    prompt: Annotated[str, Form(min_length=1, max_length=4000)],
) -> HTMLResponse:
    task = get_task(week, day)
    settings = Settings()
    if not prompt.strip():
        return task_page(request, task, settings, error="Введите непустой текст.", status_code=422)
    try:
        result = await task.run(prompt, settings)
    except ModelError as exc:
        return task_page(
            request,
            task,
            settings,
            prompt=prompt,
            error=str(exc),
            request_json=exc.request_json,
            status_code=503,
        )
    return task_page(request, task, settings, prompt=prompt, result=asdict(result))
