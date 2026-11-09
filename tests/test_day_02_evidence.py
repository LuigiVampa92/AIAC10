"""Verify archived live evidence without repeating any paid requests."""

import json
import subprocess
import sys
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from aiac.config import ROOT

RESULTS = ROOT / "docs/results/week_01/day_02/runs/2026-11-08_235011"
MODEL = "gpt-4.1-mini-2025-04-14"


class Person(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str
    age: int
    city: str


def test_archived_live_series_is_complete_and_consistent() -> None:
    report = json.loads((RESULTS / "raw.json").read_text())
    assert report["state"] == "finished"
    assert len(report["runs"]) == 15
    for series in [{"runs": report["runs"], "plan": report["plan"]}]:
        assert len(series["runs"]) == series["plan"]["total"]
        expected = {
            (variant["id"], repeat)
            for variant in series["plan"]["variants"]
            for repeat in range(1, series["plan"]["config"]["repeats"] + 1)
        }
        assert {(row["variant"], row["repeat"]) for row in series["runs"]} == expected
        for row in series["runs"]:
            assert row["status"] in {"completed", "incomplete"}
            assert row["model"] == row["request"]["model"] == MODEL
            assert row["provider"] == "openai"
            expected_prompt = series["plan"]["config"]["prompt"]
            if row["variant"] != "baseline":
                expected_prompt += "\n\n" + series["plan"]["config"]["format_instruction"]
            assert row["request"]["messages"][0]["content"] == expected_prompt
            raw = row["response"]
            choice = raw["choices"][0]
            assert row["text"] == choice["message"]["content"]
            assert row["finish_reason"] == choice["finish_reason"]
            assert row["usage"] == raw["usage"]
            assert row["usage"]["total_tokens"] == (
                row["usage"]["prompt_tokens"] + row["usage"]["completion_tokens"]
            )
            assert row["seconds"] > 0
            try:
                json.loads(row["text"])
                parsed = True
            except ValueError:
                parsed = False
            assert row["json_valid"] is parsed
            try:
                Person.model_validate_json(row["text"])
                conforms = True
            except ValidationError:
                conforms = False
            assert row["schema_valid"] is conforms


def test_archived_stop_and_limit_observations() -> None:
    report = json.loads((RESULTS / "raw.json").read_text())
    stop = [row for row in report["runs"] if row["variant"] == "stop"]
    assert len(stop) == 3
    assert all(row["request"]["stop"] == ["}"] for row in stop)
    assert all(row["json_valid"] is False for row in stop)
    limited = [row for row in report["runs"] if row["variant"] == "limit"]
    assert len(limited) == 3
    assert all(row["request"]["max_completion_tokens"] == 64 for row in limited)
    assert all(row["json_valid"] is True for row in limited)
    strict = [row for row in report["runs"] if row["variant"] == "schema"]
    assert all(row["request"]["response_format"]["json_schema"]["strict"] for row in strict)
    assert sum(row["usage"]["total_tokens"] for row in report["runs"]) == 1309


def test_live_runner_requires_explicit_flag() -> None:
    output = ROOT / ".cache" / f"unauthorized-day-02-{uuid4().hex}.json"
    process = subprocess.run(
        [sys.executable, str(ROOT / "tools/run_day_02.py"), "--output", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode != 0
    assert "require explicit --live-openai" in process.stderr
    assert not output.exists()
