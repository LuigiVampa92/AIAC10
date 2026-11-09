"""Archived live evidence checks only; never call the provider."""

import json

from aiac.config import ROOT


def test_archived_week_has_all_authorized_calls_and_reports():
    expected = {1: 1, 2: 15, 3: 50, 4: 30, 5: 15}
    total_tokens = 0
    for day, count in expected.items():
        folder = ROOT / f"docs/results/week_01/day_{day:02}/runs/2026-11-08_235011"
        report = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
        assert report["state"] == "finished"
        flat = [call for row in report["runs"] for call in row.get("calls", [row])]
        assert len(flat) == count
        assert all(call["status"] == "completed" for call in flat)
        for call in flat:
            usage = call["usage"]
            incoming = usage.get("input_tokens", usage.get("prompt_tokens"))
            outgoing = usage.get("output_tokens", usage.get("completion_tokens"))
            assert usage["total_tokens"] == incoming + outgoing
            total_tokens += usage["total_tokens"]
            assert call["request"]["model"] == call["model"]
            assert call["seconds"] > 0
        assert (folder.parent.parent / "README.md").is_file()
        assert {entry.name for entry in folder.iterdir()} == {"raw.json"}
        if day == 3:
            assert len(report["runs"]) == 40
            assert sum(len(row["calls"]) == 2 for row in report["runs"]) == 10
        if day == 4:
            assert len({(row["task"], row["temperature"]) for row in flat}) == 6
            assert all(row["request"]["reasoning"] == {"effort": "none"} for row in flat)
        if day == 5:
            assert {row["requested_model"] for row in flat} == {
                "gpt-6-luna",
                "gpt-6.1-sol",
                "gpt-6-astra",
            }
            assert all(row["cost_usd"] is not None and row["rates"]["date"] for row in flat)
    assert sum(expected.values()) == 111
    assert total_tokens == 47424
