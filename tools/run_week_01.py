"""Explicit live execution of accepted week 1 scenarios; save each result before continuing."""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from aiac.config import ROOT, Settings
from aiac.llm.catalog import list_models
from aiac.llm.client import ModelError, generate
from aiac.tasks.week_01 import day_02, day_03, day_04, day_05


def save(path: Path, report: dict[str, Any], key: str) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if key and key in text:
        raise RuntimeError("Refused unexpected sensitive content.")
    path.write_text(text, encoding="utf-8")


async def run(args: argparse.Namespace) -> None:
    settings = Settings().model_copy(update={"aiac_llm_mode": "live"})
    if settings.aiac_llm_provider != "openai" or not settings.openai_api_key:
        raise ValueError("OpenAI provider and key are required.")
    key = settings.openai_api_key.get_secret_value()
    tag = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
    for day in args.days:
        folder = ROOT / "docs/results/week_01" / f"day_{day:02}" / "runs" / tag
        folder.mkdir(parents=True, exist_ok=False)
        path = folder / "raw.json"
        report: dict[str, Any] = {
            "version": 1,
            "day": day,
            "week": 1,
            "source": "tools/run_week_01.py; real API via shared task modules (not browser E2E)",
            "created_at": datetime.now(UTC).isoformat(),
            "state": "running",
            "plan": {},
            "runs": [],
        }
        save(path, report, key)
        print(f"Day {day}: started; output {path.relative_to(ROOT)}", flush=True)

        async def record(
            row: dict[str, Any],
            report: dict[str, Any] = report,
            path: Path = path,
            day: int = day,
        ) -> None:
            report["runs"].append(row)
            save(path, report, key)
            print(
                json.dumps(
                    {
                        "day": day,
                        "calls": sum(len(r.get("calls", [r])) for r in report["runs"]),
                        "status": row.get("status", "solution"),
                        "correct": row.get("correct"),
                    }
                ),
                flush=True,
            )

        try:
            if day == 1:
                prompt = (
                    "Объясни простыми словами, что такое языковая модель, "
                    "в трёх коротких предложениях."
                )
                report["plan"] = {"calls": 1, "model": settings.aiac_llm_model, "prompt": prompt}
                save(path, report, key)
                started = perf_counter()
                result = await generate(prompt, settings)
                response = json.loads(result.response_json)
                await record(
                    {
                        **asdict(result),
                        "request": json.loads(result.request_json),
                        "response": response,
                        "usage": response.get("usage"),
                        "status": response.get("status"),
                        "seconds": round(perf_counter() - started, 3),
                    }
                )
            elif day == 2:
                config = day_02.ExperimentInput.model_validate(
                    day_02.DEFAULTS | {"selection": "all"}
                )
                plan = day_02.plan(config, settings)
                report["plan"] = plan
                save(path, report, key)
                for variant in plan["variants"]:
                    for repeat in range(1, config.repeats + 1):
                        row = await day_02.execute(
                            day_02.RunInput.model_validate(
                                config.model_dump()
                                | {
                                    "variant": variant["id"],
                                    "repeat": repeat,
                                    "model": plan["model"],
                                    "provider": plan["provider"],
                                }
                            ),
                            settings,
                        )
                        await record(row)
                        if row["status"] == "api_error":
                            raise ModelError("Day 2 API error; no retry.")
            elif day == 3:
                report["plan"] = day_03.plan(day_03.Selection())
                save(path, report, key)
                for item in report["plan"]["runs"]:
                    row = {**item, "correct": False, "calls": [], "status": "running"}
                    report["runs"].append(row)
                    prompt = ""
                    for stage in ["prepare", "solve"] if item["method"] == "meta" else ["solve"]:
                        call = await day_03.execute(
                            day_03.RunInput(**item, stage=stage, generated_prompt=prompt), settings
                        )
                        row["calls"].append(call)
                        row["status"] = call["status"]
                        if stage == "solve":
                            row["correct"] = call["correct"]
                        save(path, report, key)
                        print(
                            json.dumps(
                                {
                                    "day": day,
                                    **item,
                                    "stage": stage,
                                    "status": call["status"],
                                    "correct": call.get("correct"),
                                }
                            ),
                            flush=True,
                        )
                        if call["status"] == "api_error":
                            raise ModelError("Day 3 API error; no retry.")
                        if stage == "prepare":
                            if not call["usable_prompt"]:
                                row["status"] = "preparation_failed"
                                break
                            prompt = call["text"]
            elif day == 4:
                report["plan"] = day_04.plan(day_04.Selection())
                save(path, report, key)
                for item in report["plan"]["runs"]:
                    row = await day_04.execute(day_04.RunInput(**item), settings)
                    await record(row)
                    if row["status"] == "api_error":
                        raise ModelError("Day 4 API error; no retry.")
            else:
                ids = await list_models(settings)
                if any(model not in ids for model in day_05.DEFAULTS.values()):
                    raise ModelError(
                        "A default model is absent from the account catalog; no substitution."
                    )
                config5 = day_05.Selection(
                    **{role: {"model": model} for role, model in day_05.DEFAULTS.items()}
                )
                report["plan"] = day_05.plan(config5)
                save(path, report, key)
                for item in report["plan"]["runs"]:
                    row = await day_05.execute(day_05.RunInput(**item), settings)
                    await record(row)
                    if row["status"] == "api_error":
                        raise ModelError("Day 5 API error; no retry.")
            report["state"] = "finished"
        except ModelError as exc:
            report["state"] = "interrupted"
            report["error"] = str(exc)
            if exc.request_json:
                report["failed_request"] = json.loads(exc.request_json)
            print(f"Day {day}: stopped; safe error saved. No retry.", flush=True)
        finally:
            report["finished_at"] = datetime.now(UTC).isoformat()
            save(path, report, key)
        if report["state"] != "finished":
            break


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-openai", action="store_true")
    parser.add_argument(
        "--days", nargs="+", type=int, choices=range(1, 6), default=list(range(1, 6))
    )
    args = parser.parse_args()
    if not args.live_openai:
        parser.error("Explicit --live-openai authorization required.")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
