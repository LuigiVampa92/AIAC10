"""Verify archived live evidence without repeating any paid requests."""

import json
import subprocess
import sys
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from aiac.config import ROOT

RESULTS = ROOT / "docs/results/week_01/day_02"
MODEL = "gpt-4.1-mini-2025-04-14"


class Person(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str
    age: int
    city: str


def test_archived_live_series_is_complete_and_consistent() -> None:
    report = json.loads((RESULTS / "experiment.json").read_text())
    assert report["state"] == "finished"
    assert len(report["series"]) == 2
    assert [len(series["runs"]) for series in report["series"]] == [15, 3]
    for series in report["series"]:
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


def test_archived_stop_and_boundary_observations() -> None:
    report = json.loads((RESULTS / "experiment.json").read_text())
    stop = [row for row in report["series"][0]["runs"] if row["variant"] == "stop"]
    assert len(stop) == 3
    assert all(row["request"]["stop"] == ["}"] for row in stop)
    assert all(row["json_valid"] is False for row in stop)
    stress = report["series"][1]["runs"]
    assert all(row["request"]["max_completion_tokens"] == 16 for row in stress)
    assert all(row["finish_reason"] == "length" for row in stress)
    # This is an archived observation, not an expectation for future stochastic runs.
    assert all(row["json_valid"] is True for row in stress)
    probe = json.loads((RESULTS / "compatibility_probe.json").read_text())
    assert probe["request"]["stop"] == ["END"]
    assert probe["response"]["model"] == MODEL
    assert probe["text"] == "OK"
    assert probe["response"]["choices"][0]["finish_reason"] == "stop"
    total = probe["usage"]["total_tokens"] + sum(
        row["usage"]["total_tokens"] for series in report["series"] for row in series["runs"]
    )
    assert total == 1578


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
