import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt

from aiac.agents.storage import HistoryError
from aiac.config import Settings
from aiac.llm.catalog import list_models
from aiac.llm.client import ModelError
from aiac.reports.markdown import render as render_report
from aiac.tasks.registry import TASKS, Task
from aiac.tasks.week_01 import day_02, day_03, day_04, day_05
from aiac.tasks.week_02 import day_01 as day_06
from aiac.tasks.week_02 import day_02 as day_07

WEB = Path(__file__).parent
app = FastAPI(title="AIAC · 35 заданий")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")


def asset_versions(request: Request) -> dict[str, int]:
    return {"style_version": (WEB / "static" / "style.css").stat().st_mtime_ns}


templates = Jinja2Templates(directory=WEB / "templates", context_processors=[asset_versions])
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
        name=(
            f"day_{task.day:02}.html"
            if task.week == 1 and task.day in (2, 3, 4, 5)
            else "week_02_day_01.html"
            if (task.week, task.day) == (2, 1)
            else "week_02_day_02.html"
            if (task.week, task.day) == (2, 2)
            else "task.html"
        ),
        context={
            "task": task,
            "description": markdown.render(description),
            "mode": settings.aiac_llm_mode,
            "provider": settings.aiac_llm_provider,
            "model": display_settings.aiac_llm_model if settings.aiac_llm_mode == "live" else "",
            "prompt": prompt,
            "agent_examples": day_06.EXAMPLES,
            "chat_script_version": (WEB / "static" / "week_02_day_02.js").stat().st_mtime_ns,
            "agent_script_version": (WEB / "static" / "week_02_day_01.js").stat().st_mtime_ns,
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
            "day_04_model": day_04.MODEL,
            "day_04_tasks": day_04.TASKS,
            "day_04_script_version": (WEB / "static" / "day_04.js").stat().st_mtime_ns,
            "day_05_defaults": day_05.DEFAULTS,
            "day_05_roles": day_05.ROLES,
            "day_05_tasks": day_03.TASKS,
            "day_05_script_version": (WEB / "static" / "day_05.js").stat().st_mtime_ns,
            "day_01_report": (
                {
                    "day": 1,
                    "state": "finished",
                    "created_at": datetime.now(UTC).isoformat(),
                    "plan": {"calls": 1, "request": json.loads(result["request_json"] or "{}")},
                    "runs": [
                        {
                            **result,
                            "request": json.loads(result["request_json"] or "{}"),
                            "response": json.loads(result["response_json"] or "{}"),
                            "status": json.loads(result["response_json"] or "{}").get("status"),
                            "usage": json.loads(result["response_json"] or "{}").get("usage"),
                        }
                    ],
                }
                if result and (task.week, task.day) == (1, 1)
                else None
            ),
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
    if (week == 1 and day in (2, 3, 4, 5)) or (week == 2 and day in (1, 2)):
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


@app.post("/weeks/1/days/4/plan")
async def day_04_plan(config: day_04.Selection) -> JSONResponse:
    return JSONResponse(day_04.plan(config))


@app.post("/weeks/1/days/4/runs")
async def day_04_run(config: day_04.RunInput) -> JSONResponse:
    try:
        return JSONResponse(await day_04.execute(config, Settings()))
    except ModelError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.get("/weeks/1/days/5/models")
async def day_05_models() -> JSONResponse:
    try:
        ids = await list_models(Settings())
        return JSONResponse(
            {"models": [{"id": model, "rates": day_05.default_rates(model)} for model in ids]},
            headers={"Cache-Control": "no-store"},
        )
    except ModelError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.post("/weeks/1/days/5/plan")
async def day_05_plan(config: day_05.Selection) -> JSONResponse:
    return JSONResponse(day_05.plan(config))


@app.post("/weeks/1/days/5/runs")
async def day_05_run(config: day_05.RunInput) -> JSONResponse:
    try:
        return JSONResponse(await day_05.execute(config, Settings()))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except ModelError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.post("/weeks/1/days/{day}/report")
async def download_report(day: int, request: Request) -> Response:
    if day not in range(1, 6):
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    body = await request.body()
    if len(body) > 8_000_000:
        raise HTTPException(status_code=413, detail="Слишком большой отчёт")
    try:
        snapshot = json.loads(body)
        if (
            not isinstance(snapshot, dict)
            or not isinstance(snapshot.get("plan"), dict)
            or not isinstance(snapshot.get("runs"), list)
            or len(snapshot["runs"]) > 1500
        ):
            raise ValueError("Invalid snapshot")
        text = render_report(day, snapshot)
    except (ValueError, TypeError, KeyError, AttributeError):
        return JSONResponse({"error": "Некорректные данные отчёта."}, status_code=422)
    return Response(
        text,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="week-01-day-{day:02}.md"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/weeks/2/days/1/run")
async def day_06_run(config: day_06.AgentInput) -> JSONResponse:
    try:
        return JSONResponse(await day_06.execute(config, Settings()))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except ModelError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.get("/weeks/2/days/2/history")
async def day_07_history() -> JSONResponse:
    try:
        return JSONResponse(day_07.read(), headers={"Cache-Control": "no-store"})
    except HistoryError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)


@app.post("/weeks/2/days/2/new")
async def day_07_new() -> JSONResponse:
    try:
        return JSONResponse(day_07.new(), headers={"Cache-Control": "no-store"})
    except HistoryError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)


@app.post("/weeks/2/days/2/run")
async def day_07_run(config: day_06.AgentInput) -> JSONResponse:
    try:
        return JSONResponse(
            await day_07.execute(config, Settings()), headers={"Cache-Control": "no-store"}
        )
    except HistoryError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except ModelError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
