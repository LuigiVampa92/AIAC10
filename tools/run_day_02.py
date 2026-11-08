"""Explicitly authorized live day 2 experiments; no API calls without --live-openai."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiac.config import ROOT, Settings
from aiac.tasks.week_01.day_02 import DEFAULTS, ExperimentInput, RunInput, execute, plan


def save(path: Path, report: dict[str, Any], key: str) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if key and key in text:
        raise RuntimeError("Refused to save unexpected sensitive content.")
    path.write_text(text, encoding="utf-8")


async def run(args: argparse.Namespace) -> None:
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Output must be a new file inside the project.")
    settings = Settings().model_copy(update={"aiac_llm_mode": "live"})
    if args.model:
        settings = settings.model_copy(update={"aiac_day_02_model": args.model})
    main = ExperimentInput.model_validate(DEFAULTS | {"selection": "all"})
    configs = [("main", main)]
    if args.include_limit_stress:
        configs.append(
            ("limit_stress", main.model_copy(update={"selection": "limit", "token_limit": 16}))
        )
    plans = [(name, config, plan(config, settings)) for name, config in configs]
    total = sum(item[2]["total"] for item in plans)
    print(f"Authorized live experiment: up to {total} API calls; no retries.", flush=True)
    report: dict[str, Any] = {
        "version": 1,
        "task": "week_01/day_02",
        "source": "tools/run_day_02.py",
        "created_at": datetime.now(UTC).isoformat(),
        "state": "running",
        "series": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8"):
        pass
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    save(output, report, key)
    for name, config, experiment_plan in plans:
        series: dict[str, Any] = {"name": name, "plan": experiment_plan, "runs": []}
        report["series"].append(series)
        for variant in experiment_plan["variants"]:
            for repeat in range(1, config.repeats + 1):
                request = RunInput.model_validate(
                    config.model_dump()
                    | {
                        "variant": variant["id"],
                        "repeat": repeat,
                        "model": experiment_plan["model"],
                        "provider": experiment_plan["provider"],
                    }
                )
                result = await execute(request, settings)
                series["runs"].append(result)
                if result["status"] == "api_error":
                    report["state"] = "api_error"
                save(output, report, key)
                print(
                    json.dumps(
                        {
                            "series": name,
                            "variant": variant["id"],
                            "repeat": repeat,
                            "status": result["status"],
                            "json_valid": result["json_valid"],
                            "schema_valid": result["schema_valid"],
                        }
                    ),
                    flush=True,
                )
                if result["status"] == "api_error":
                    raise RuntimeError(
                        "API rejected the experiment; partial report saved, no retry."
                    )
    report["state"] = "finished"
    report["finished_at"] = datetime.now(UTC).isoformat()
    save(output, report, key)
    print(f"Saved {total} results to a project-local JSON report.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-openai", action="store_true", help="Authorize real paid API calls")
    parser.add_argument("--model", help="Explicit override for this run only; does not edit .env")
    parser.add_argument(
        "--include-limit-stress", action="store_true", help="3 extra limit=16 calls"
    )
    parser.add_argument("--output", required=True, help="New JSON file inside project")
    args = parser.parse_args()
    if not args.live_openai:
        parser.error("Real API calls require explicit --live-openai.")
    try:
        asyncio.run(run(args))
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
