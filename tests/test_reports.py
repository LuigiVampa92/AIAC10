from fastapi.testclient import TestClient

from aiac.reports.markdown import fence, render
from aiac.web.app import app


def call(**kwargs):
    return {
        "status": "completed",
        "correct": True,
        "text": "answer",
        "model": "test",
        "seconds": 1,
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        **kwargs,
    }


def test_meta_costs_and_missing_usage():
    report = {
        "state": "finished",
        "plan": {"calls": 2, "runs": [{"task": "price", "method": "meta", "effort": "max"}]},
        "runs": [
            {
                "method": "meta",
                "effort": "max",
                "correct": True,
                "calls": [call(stage="prepare"), call(stage="solve")],
            }
        ],
    }
    text = render(3, report)
    assert "20 / 40 / 60" in text
    assert "| meta / max | 1/1 | 1 | 0 |" in text
    report["runs"][0]["calls"][1]["usage"] = None
    assert "нет данных / нет данных / нет данных" in render(3, report)


def test_zero_temperature_and_api_error_not_success():
    row = call(task="price", temperature=0, repeat=1, format_valid=True, normalized="4494")
    report = {"state": "stopped", "plan": {"calls": 5, "runs": [row] * 5}, "runs": [row]}
    text = render(4, report)
    assert "1/5" in text
    assert "Все завершённые тексты совпали" not in text
    assert "| 0 | 1 | 1 | 0 |" in text
    report["runs"] = [call(status="api_error", text="", correct=False)]
    assert "Успешный реальный запрос" in render(1, report)


def test_cost_comparison_only_for_full_matching_series():
    rows = [
        call(role="small", task="price", cost_usd=0.01),
        call(role="flagship", task="price", cost_usd=0.03),
    ]
    report = {
        "plan": {
            "calls": 2,
            "runs": rows,
            "repeats": 1,
            "tasks": [{"id": "price", "title": "Покупка"}],
        },
        "runs": rows,
    }
    text = render(5, report)
    assert "3.00×" in text
    assert "0.0 п.п." in text
    assert "Маленькой модели хватило" in text
    report["runs"] = rows[:1]
    assert "3.00×" not in render(5, report)


def test_fence_handles_model_markdown():
    assert fence("```\n# injected") == "````\n```\n# injected\n````"


def test_download_is_offline_and_pages_have_buttons():
    with TestClient(app) as client:
        for day in range(1, 6):
            assert "Скачать отчёт" in client.get(f"/weeks/1/days/{day}").text
            response = client.post(
                f"/weeks/1/days/{day}/report",
                json={"plan": {"calls": 1}, "runs": [call()], "state": "finished"},
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/markdown")
            assert "attachment" in response.headers["content-disposition"]
        assert (
            client.post("/weeks/1/days/3/report", json={"plan": [], "runs": []}).status_code == 422
        )
        assert client.post("/weeks/1/days/9/report", json={}).status_code == 404
