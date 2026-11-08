from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt

from aiac.config import Settings
from aiac.llm.client import ModelError
from aiac.tasks.registry import TASKS, Task
from aiac.tasks.week_01 import day_02, day_03

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
    display_settings = (
        day_02.experiment_settings(settings) if (task.week, task.day) == (1, 2) else settings
    )
    return templates.TemplateResponse(
        request=request,
        name=(f"day_{task.day:02}.html" if task.week == 1 and task.day in (2, 3) else "task.html"),
        context={
            "task": task,
            "description": markdown.render(description),
            "mode": settings.aiac_llm_mode,
            "provider": settings.aiac_llm_provider,
            "model": display_settings.aiac_llm_model if settings.aiac_llm_mode == "live" else "",
            "prompt": prompt,
            "result": result,
            "error": error,
            "request_json": request_json,
            "experiment_defaults": day_02.DEFAULTS,
            "variants": day_02.VARIANTS,
            "day_02_script_version": (WEB / "static" / "day_02.js").stat().st_mtime_ns,
            "day_03_model": day_03.MODEL,
            "day_03_tasks": day_03.TASKS,
            "day_03_methods": day_03.METHODS,
            "day_03_script_version": (WEB / "static" / "day_03.js").stat().st_mtime_ns,
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
    if week == 1 and day in (2, 3):
        return task_page(
            request,
            task,
            settings,
            error="Используйте форму эксперимента этого дня.",
            status_code=422,
        )
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


@app.post("/weeks/1/days/2/plan")
async def day_02_plan(config: day_02.ExperimentInput) -> JSONResponse:
    try:
        return JSONResponse(day_02.plan(config, Settings()))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)


@app.post("/weeks/1/days/2/runs")
async def day_02_run(config: day_02.RunInput) -> JSONResponse:
    try:
        return JSONResponse(await day_02.execute(config, Settings()))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except ModelError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.post("/weeks/1/days/3/plan")
async def day_03_plan(config: day_03.Selection) -> JSONResponse:
    try:
        return JSONResponse(day_03.plan(config))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)


@app.post("/weeks/1/days/3/runs")
async def day_03_run(config: day_03.RunInput) -> JSONResponse:
    try:
        return JSONResponse(await day_03.execute(config, Settings()))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except ModelError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
