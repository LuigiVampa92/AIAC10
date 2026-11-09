"""Render the same brief report for browser downloads and authorized CLI runs."""

import json
import math
from collections import defaultdict
from typing import Any, cast

TITLES = {
    1: "Первый запрос к LLM",
    2: "Формат ответа",
    3: "Способы рассуждения",
    4: "Температура",
    5: "Версии моделей",
}


def cell(value: Any) -> str:
    if value is None:
        return "нет данных"
    if isinstance(value, float):
        value = f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def fence(text: str) -> str:
    marker = "```"
    while marker in text:
        marker += "`"
    return f"{marker}\n{text}\n{marker}"


def total(values: list[Any]) -> float | int | None:
    return (
        sum(values)
        if values and all(type(v) in (int, float) and math.isfinite(v) for v in values)
        else None
    )


def calls(row: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], row.get("calls", [row]))


def token(call: dict[str, Any], key: str) -> Any:
    usage = call.get("usage") or {}
    return usage.get(
        key,
        usage.get(
            {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens"}.get(key, key)
        ),
    )


def group_key(day: int, row: dict[str, Any]) -> str:
    if day == 2:
        return str(row.get("label", row.get("variant", "неизвестно")))
    if day == 3:
        return f"{row.get('method')} / {row.get('effort')}"
    if day == 4:
        return f"{row.get('task')} / temperature={row.get('temperature')}"
    if day == 5:
        return str(row.get("role"))
    return "API-запрос"


def render(day: int, report: dict[str, Any]) -> str:
    if day not in TITLES:
        raise ValueError("Отчёты поддерживаются только для дней 1–5 первой недели.")
    rows = report.get("runs", [])
    plan = report.get("plan", {})
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[group_key(day, row)].append(row)
    flat = [call for row in rows for call in calls(row)]
    expected = plan.get("calls", plan.get("total"))
    if day == 2 and expected is None:
        expected = len(plan.get("variants", [])) * plan.get("config", {}).get("repeats", 3)
    planned_groups: dict[str, int] = defaultdict(int)
    for item in plan.get("runs", []):
        planned_groups[group_key(day, item)] += 1
    models = sorted({str(call.get("model")) for call in flat if call.get("model")})
    lines = [
        f"# Неделя 1 · День {day}: {TITLES[day]}",
        "",
        "## Условия и полнота",
        "",
        f"- Начало: {cell(report.get('created_at', plan.get('created_at')))}.",
        f"- Состояние серии: {cell(report.get('state', 'частичный снимок'))}.",
        f"- Обработано вызовов: {len(flat)}; запланировано: {cell(expected)}.",
        f"- Фактические модели: {', '.join(models) or 'нет данных'}.",
        f"- Источник: {cell(report.get('source', 'снимок текущей страницы'))}.",
        "- Ошибки и неполные ответы не считаются успешными;\n"
        "неизвестные метрики не заменены нулями.",
        "",
    ]
    if report.get("error"):
        lines += ["Причина остановки:", "", fence(str(report["error"])), ""]
    if any(call.get("mode") == "demo" for call in flat):
        lines += ["**Демонстрационная заглушка: эта серия не подтверждает реальный API.**", ""]
    lines += ["## Сводная таблица", ""]
    if day == 2:
        headers = [
            "Вариант",
            "Ответов",
            "Завершено",
            "JSON",
            "По схеме",
            "Ошибок / неизвестно",
            "Токены вход / выход / всего",
            "Время, с",
        ]
    else:
        headers = [
            "Комбинация",
            "Обработано / план",
            "Верно",
            "Неполных / отказов / ошибок",
            "Токены вход / выход / всего",
            "Время, с",
        ]
        if day == 4:
            headers += [
                "Ошибок формата",
                "Разных текстов",
                "Разных итогов / наборов",
                "Разных названий",
            ]
        if day == 5:
            headers += ["Среднее время ответа, с", "Стоимость, USD"]
    lines += ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    stats: dict[str, dict[str, Any]] = {}
    for key, group in grouped.items():
        all_calls = [call for row in group for call in calls(row)]
        replies = [
            c
            for c in all_calls
            if c.get("status") in ("completed", "incomplete", "refusal", "empty")
        ]
        failures = sum(c.get("status") != "completed" for c in all_calls)
        correct = sum(row.get("correct") is True for row in group)
        seconds = total([c.get("seconds") for c in all_calls])
        tokens = " / ".join(
            cell(total([token(c, k) for c in all_calls]))
            for k in ("input_tokens", "output_tokens", "total_tokens")
        )
        expected_group = planned_groups.get(key)
        if day == 1:
            expected_group = expected
        if day == 2:
            values: list[Any] = [
                key,
                len(replies),
                sum(c.get("status") == "completed" for c in all_calls),
                f"{sum(c.get('json_valid') is True for c in replies)}/{len(replies)}",
                f"{sum(c.get('schema_valid') is True for c in replies)}/{len(replies)}",
                sum(c.get("status") in ("api_error", "unknown") for c in all_calls),
                tokens,
                seconds,
            ]
        else:
            label = (
                f"{key} · {plan.get('models', {}).get(key, {}).get('model', 'неизвестно')}"
                if day == 5
                else key
            )
            values = [
                label,
                f"{len(group)}/{expected_group or 'нет данных'}",
                correct if day != 1 and not (day == 4 and group[0].get("task") == "names") else "—",
                failures,
                tokens,
                seconds,
            ]
        valid = [c for c in replies if c.get("format_valid") is True]
        unique_texts = len({c.get("text", "") for c in replies})
        if day == 4:
            values += [
                sum(c.get("format_valid") is False for c in replies),
                unique_texts,
                len({json.dumps(c.get("normalized"), ensure_ascii=False) for c in valid}),
                len({name for c in valid for name in c.get("names", [])}),
            ]
        price = total([c.get("cost_usd") for c in all_calls])
        if day == 5:
            reply_seconds = total([c.get("seconds") for c in replies])
            values += [reply_seconds / len(replies) if reply_seconds is not None else None, price]
        lines.append("| " + " | ".join(cell(v) for v in values) + " |")
        stats[key] = {
            "correct": correct,
            "count": len(group),
            "expected": expected_group,
            "failures": failures,
            "cost": price,
            "seconds": seconds,
        }
    lines += [
        "",
        "Время — сумма API-операций, не чистое время генерации.\n"
        "Reasoning входит в выходные токены.",
        "",
        "## Наблюдения и выводы",
        "",
    ]
    if day == 1:
        success = bool(
            flat and flat[0].get("status") == "completed" and flat[0].get("text", "").strip()
        )
        lines += [
            "Реальный запрос вернул непустой завершённый ответ."
            if success and flat[0].get("mode") == "live"
            else "Успешный реальный запрос этим снимком не подтверждён."
        ]
    elif day == 2:
        for key, group in grouped.items():
            n = len(group)
            lines.append(
                f"- {cell(key)}: JSON {sum(r.get('json_valid') is True for r in group)}/{n}, "
                f"схема соблюдена {sum(r.get('schema_valid') is True for r in group)}/{n}."
            )
        lines += [
            "",
            "Формат, схема и завершение проверяются отдельно.\n"
            "finish_reason=stop не доказывает срабатывание маркера.",
        ]
    elif day == 3:
        for method in ("direct", "steps", "meta", "experts"):
            low, high = stats.get(f"{method} / none"), stats.get(f"{method} / max")
            if low and high:
                lines.append(
                    f"- {method}: none {low['correct']}/{low['expected']}, "
                    f"{cell(low['seconds'])} с; "
                    f"max — {high['correct']}/{high['expected']} верно, {cell(high['seconds'])} с."
                )
        for effort in ("none", "max"):
            baseline = stats.get(f"direct / {effort}")
            if baseline and baseline["count"] == baseline["expected"] and not baseline["failures"]:
                same = [
                    m
                    for m in ("steps", "meta", "experts")
                    if f"{m} / {effort}" in stats
                    and stats[f"{m} / {effort}"]["count"] == stats[f"{m} / {effort}"]["expected"]
                    and not stats[f"{m} / {effort}"]["failures"]
                    and stats[f"{m} / {effort}"]["correct"] <= baseline["correct"]
                ]
                if same:
                    lines.append(
                        f"- При {effort} не увеличили число верных ответов относительно direct: "
                        f"{', '.join(same)}. Это не гарантирует равенства затрат."
                    )
        lines += [
            "",
            "Метапромптинг учитывает оба этапа. Оценивается answer;\n"
            "объяснение не исправляет неверный итог.",
        ]
    elif day == 4:
        for key, group in grouped.items():
            texts = {
                c.get("text") for r in group for c in calls(r) if c.get("status") == "completed"
            }
            lines.append(
                f"- {cell(key)}: {len(texts)} разных завершённых текстов; попыток {len(group)}."
            )
            if group[0].get("temperature") == 0:
                full = (
                    len(group) >= 2
                    and len(group) == planned_groups.get(key)
                    and all(r.get("status") == "completed" for r in group)
                )
                lines.append(
                    "  "
                    + (
                        "Все завершённые тексты совпали в этой серии;\n"
                        "это не гарантия воспроизводимости."
                        if len(texts) == 1 and full
                        else "Тексты различались даже при temperature=0."
                        if len(texts) > 1 and full
                        else "Один ответ или неполная серия не подтверждают повторяемость."
                    )
                )
        lines += [
            "",
            "В покупке разные неверные итоги не полезны.\n"
            "Разнообразие названий может помогать выбору.\n"
            "Качество и уместность оцените по примерам.",
        ]
    else:
        small, flagship = stats.get("small"), stats.get("flagship")
        if (
            small
            and flagship
            and all(s["expected"] == s["count"] and not s["failures"] for s in (small, flagship))
        ):
            delta = (
                flagship["correct"] / flagship["count"] - small["correct"] / small["count"]
            ) * 100
            lines.append(f"- Разница точности флагман − маленькая: {delta:.1f} п.п.")
            if small["cost"] is not None and small["cost"] > 0 and flagship["cost"] is not None:
                lines.append(
                    f"- Расходы флагман / маленькая: {flagship['cost'] / small['cost']:.2f}×."
                )
        tasks = plan.get("tasks", [])
        lines += ["", "| Задача | Маленькая | Средняя | Флагманская |", "| --- | --- | --- | --- |"]
        for task in tasks:
            task_values = [task["title"]]
            for role in ("small", "medium", "flagship"):
                attempts = [
                    r for r in rows if r.get("role") == role and r.get("task") == task["id"]
                ]
                task_values.append(
                    f"{sum(r.get('correct') is True for r in attempts)}/{plan.get('repeats', 1)}"
                )
            lines.append("| " + " | ".join(cell(v) for v in task_values) + " |")
        lines.append("")
        for task in tasks:
            group = [r for r in rows if r.get("role") == "small" and r.get("task") == task["id"]]
            if len(group) == plan.get("repeats", 1) and all(
                r.get("correct") is True for r in group
            ):
                lines.append(
                    f"- Маленькой модели хватило для «{cell(task['title'])}» во всех повторах."
                )
        lines += [
            "",
            "Стоимость расчётная, не выписка биллинга.\n"
            "Различия reasoning и длины объяснений влияют на сравнение.",
        ]
    lines += ["", "## Примеры ответов", ""]
    for key, group in grouped.items():
        candidates = [c for r in group for c in calls(r) if c.get("text")]
        if candidates:
            solved = [c for c in candidates if c.get("stage") != "prepare"]
            sample = next(
                (c for c in solved if c.get("correct") is False),
                solved[0] if solved else candidates[0],
            )
            lines += [f"### {cell(key)}", "", fence(str(sample["text"])), ""]
    if flat:
        lines += [
            "## Пример фактического запроса",
            "",
            fence(json.dumps(flat[0].get("request", {}), ensure_ascii=False, indent=2)),
            "",
        ]
    settings = {k: v for k, v in plan.items() if k not in ("runs", "tasks", "prompts")}
    lines += [
        "## Зафиксированные настройки",
        "",
        fence(json.dumps(settings, ensure_ascii=False, indent=2)),
        "",
        "## Ограничения",
        "",
        "Отчёт — снимок серии. Частичная серия не считается полной.\n"
        "Нет данных — не нулевой расход.\n"
        "Исходные JSON нужны для проверки вызовов.",
        "",
    ]
    return "\n".join(lines)
