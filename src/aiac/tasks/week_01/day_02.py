"""Controlled output experiments: planning, payloads and independent JSON validation."""

import json
import math
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from aiac.config import Settings
from aiac.llm.chat import ChatError, complete
from aiac.llm.client import ModelError, ModelResult

Variant = Literal["baseline", "format", "limit", "stop", "schema"]
Selection = Literal["all", "baseline", "format", "limit", "stop", "schema"]
VARIANTS: dict[str, str] = {
    "baseline": "Базовый",
    "format": "Формат в промпте",
    "limit": "Формат + лимит",
    "stop": "Формат + stop",
    "schema": "Строгая схема",
}
DEFAULT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
        "city": {"type": "string"},
    },
    "required": ["name", "age", "city"],
    "additionalProperties": False,
}
DEFAULTS = {
    "prompt": "Извлеки имя, возраст и город из текста: Анна, 28 лет, живёт в Казани.",
    "format_instruction": (
        "Верни только JSON-объект с полями name (строка), age (целое число), city (строка). "
        "Без пояснений, Markdown и дополнительных полей."
    ),
    "token_limit": 64,
    "stop_sequence": "}",
    "repeats": 3,
    "schema_text": json.dumps(DEFAULT_SCHEMA, ensure_ascii=False, indent=2),
}


def strict_json(text: str) -> Any:
    def reject(value: str) -> Any:
        raise ValueError("Недопустимая JSON-константа.")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Повторяющийся ключ JSON.")
            result[key] = value
        return result

    def number(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("Число вне допустимого диапазона.")
        return parsed

    return json.loads(text, parse_constant=reject, parse_float=number, object_pairs_hook=pairs)


def json_equal(left: Any, right: Any) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        return bool(left == right)
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            json_equal(value, right[key]) for key, value in left.items()
        )
    return bool(left == right)


def check_schema(schema: Any, depth: int = 0) -> None:
    if depth > 8 or not isinstance(schema, dict):
        raise ValueError("Схема должна быть объектом; глубина не более 8.")
    kind = schema.get("type")
    if kind not in {"object", "array", "string", "integer", "number", "boolean", "null"}:
        raise ValueError("Укажите один поддерживаемый type для каждого узла схемы.")
    allowed = {"type", "enum"}
    if kind == "object":
        allowed |= {"properties", "required", "additionalProperties"}
        props = schema.get("properties")
        required = schema.get("required")
        if not isinstance(props, dict) or len(props) > 30:
            raise ValueError("У объекта нужны properties (не более 30 полей).")
        if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
            raise ValueError("required должен содержать имена всех полей.")
        if len(required) != len(props) or set(required) != set(props):
            raise ValueError("Все properties должны входить в required ровно один раз.")
        if schema.get("additionalProperties") is not False:
            raise ValueError("Для объекта нужно additionalProperties: false.")
        for child in props.values():
            check_schema(child, depth + 1)
    if kind == "array":
        allowed.add("items")
        check_schema(schema.get("items"), depth + 1)
    if set(schema) - allowed:
        raise ValueError(
            "Поддерживаются только type, enum, properties, required, additionalProperties, items."
        )
    if "enum" in schema:
        options = schema["enum"]
        if not isinstance(options, list) or not 1 <= len(options) <= 30:
            raise ValueError("enum должен содержать от 1 до 30 значений.")
        if any(json_equal(a, b) for index, a in enumerate(options) for b in options[index + 1 :]):
            raise ValueError("enum не должен содержать повторяющиеся значения.")
        for option in options:
            if schema_errors(
                option, {key: value for key, value in schema.items() if key != "enum"}
            ):
                raise ValueError("Значения enum должны соответствовать типу и структуре схемы.")


def schema_errors(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    kind = schema["type"]
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int or (type(value) is float and value.is_integer()),
        "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "null": value is None,
    }
    if not matches[kind]:
        return [f"{path}: ожидается {kind}."]
    errors = []
    if "enum" in schema and not any(json_equal(value, option) for option in schema["enum"]):
        errors.append(f"{path}: значение не входит в enum.")
    if kind == "object":
        for key in schema["required"]:
            if key not in value:
                errors.append(f"{path}.{key}: отсутствует обязательное поле.")
        for key, child in value.items():
            if key not in schema["properties"]:
                errors.append(f"{path}.{key}: лишнее поле.")
            else:
                errors.extend(schema_errors(child, schema["properties"][key], f"{path}.{key}"))
    if kind == "array":
        for index, child in enumerate(value):
            errors.extend(schema_errors(child, schema["items"], f"{path}[{index}]"))
    return errors[:30]


class ExperimentInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    format_instruction: str = Field(min_length=1, max_length=2000)
    token_limit: int = Field(ge=16, le=512)
    stop_sequence: str = Field(min_length=1, max_length=100)
    schema_text: str = Field(min_length=2, max_length=12000)
    repeats: int = Field(ge=2, le=5)
    selection: Selection = "all"

    @field_validator("prompt", "format_instruction", "stop_sequence")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Заполните поле непустым текстом.")
        return value

    def schema_value(self) -> dict[str, Any]:
        try:
            value = strict_json(self.schema_text)
            check_schema(value)
            if value["type"] != "object":
                raise ValueError("Корень строгой схемы должен иметь type: object.")
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"JSON Schema: ошибка JSON, строка {exc.lineno}, столбец {exc.colno}."
            ) from exc
        except ValueError as exc:
            raise ValueError(f"JSON Schema: {exc}") from exc
        except (TypeError, RecursionError) as exc:
            raise ValueError("Некорректная или неподдерживаемая JSON Schema.") from exc
        return dict(value)


class RunInput(ExperimentInput):
    variant: Variant
    repeat: int = Field(ge=1, le=5)
    model: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=100)


def require_live(settings: Settings) -> None:
    if settings.aiac_llm_mode != "live" or settings.aiac_llm_provider != "openai":
        raise ValueError("Для эксперимента включите live и провайдер openai в настройках.")
    if not settings.aiac_llm_model.strip() or not settings.openai_api_key:
        raise ValueError("Настройте AIAC_LLM_MODEL и OPENAI_API_KEY.")
    if not settings.openai_api_key.get_secret_value().strip():
        raise ValueError("Настройте OPENAI_API_KEY.")


def experiment_settings(settings: Settings) -> Settings:
    """An explicit day-specific model leaves the global model and other days intact."""
    if settings.aiac_day_02_model.strip():
        return settings.model_copy(update={"aiac_llm_model": settings.aiac_day_02_model.strip()})
    return settings


def plan(config: ExperimentInput, settings: Settings) -> dict[str, Any]:
    settings = experiment_settings(settings)
    require_live(settings)
    config.schema_value()
    variants = list(VARIANTS) if config.selection == "all" else [config.selection]
    return {
        "model": settings.aiac_llm_model,
        "provider": settings.aiac_llm_provider,
        "endpoint": "/v1/chat/completions",
        "config": config.model_dump(),
        "variants": [{"id": key, "label": VARIANTS[key]} for key in variants],
        "total": len(variants) * config.repeats,
        "created_at": datetime.now(UTC).isoformat(),
    }


def payload_for(config: ExperimentInput, variant: Variant, model: str) -> dict[str, Any]:
    schema = config.schema_value()
    prompt = config.prompt
    if variant != "baseline":
        prompt += "\n\n" + config.format_instruction
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": config.token_limit if variant == "limit" else 512,
    }
    if variant == "stop":
        payload["stop"] = [config.stop_sequence.replace("\\n", "\n")]
    if variant == "schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "experiment_result", "strict": True, "schema": schema},
        }
    return payload


async def execute(config: RunInput, settings: Settings) -> dict[str, Any]:
    settings = experiment_settings(settings)
    require_live(settings)
    if config.model != settings.aiac_llm_model or config.provider != settings.aiac_llm_provider:
        raise ValueError("Настройки модели изменились. Создайте новый эксперимент.")
    if config.repeat > config.repeats:
        raise ValueError("Номер повтора превышает число повторов.")
    if config.selection != "all" and config.variant != config.selection:
        raise ValueError("Вариант не входит в выбранный эксперимент.")
    payload = payload_for(config, config.variant, config.model)
    result: dict[str, Any] = {
        "variant": config.variant,
        "label": VARIANTS[config.variant],
        "repeat": config.repeat,
        "provider": config.provider,
        "model": config.model,
        "request": payload,
        "endpoint": "/v1/chat/completions",
        "created_at": datetime.now(UTC).isoformat(),
        "text": "",
        "response": None,
        "usage": None,
        "json_valid": None,
        "schema_valid": None,
        "validation_errors": [],
    }
    started = perf_counter()
    try:
        response = await complete(payload, settings)
    except ChatError as exc:
        return result | {
            "status": "api_error",
            "error": str(exc),
            "stop_batch": exc.stop_batch,
            "response": exc.response,
            "seconds": round(perf_counter() - started, 3),
        }
    result.update(
        {
            "model": response.model,
            "text": response.text,
            "response": response.response,
            "usage": response.usage,
            "seconds": response.seconds,
            "finish_reason": response.finish_reason,
            "refusal": response.refusal,
            "status": "refusal"
            if response.refusal
            else (
                "completed"
                if response.finish_reason == "stop" and response.text.strip()
                else "incomplete"
            ),
        }
    )
    if response.text:
        try:
            parsed = strict_json(response.text)
            errors = schema_errors(parsed, config.schema_value())
            result.update(json_valid=True, schema_valid=not errors, validation_errors=errors)
        except (ValueError, RecursionError):
            result.update(
                json_valid=False,
                schema_valid=False,
                validation_errors=["Исходный ответ не разбирается как строгий JSON."],
            )
    return result


async def run(prompt: str, settings: Settings) -> ModelResult:
    raise ModelError("Используйте форму эксперимента второго дня.")
