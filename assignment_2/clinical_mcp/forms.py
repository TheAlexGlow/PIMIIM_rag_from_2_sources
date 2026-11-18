"""Resolve common JSON Schema field shapes and prepare explicit demo inputs."""

import json
import math


def field_schema(schema: dict, field: dict) -> dict:
    field = dict(field)
    ref = field.pop("$ref", None)
    if ref and ref.startswith("#/$defs/"):
        field = {**schema.get("$defs", {}).get(ref.split("/")[-1], {}), **field}
    for keyword in ("anyOf", "oneOf"):
        choices = field.get(keyword, [])
        non_null = [choice for choice in choices if choice.get("type") != "null"]
        if len(non_null) == 1:
            return {**field_schema(schema, non_null[0]), **{k: v for k, v in field.items() if k != keyword}}
    return field


def sanitize(schema: dict, values: dict, example: dict | None = None) -> tuple[dict, list[str]]:
    """Coerce transport types. Only an explicitly requested example fills blanks."""
    arguments = {}
    changes = []
    required = set(schema.get("required", []))
    for key, raw_field in schema.get("properties", {}).items():
        field = field_schema(schema, raw_field)
        label = field.get("title", key)
        value = values.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if example is not None and key in example:
                value = example[key]
                changes.append(f"{label} · учебное значение {value}")
            elif key in required:
                raise ValueError(f"Заполните поле «{label}».")
            else:
                continue
        kind = field.get("type")
        if kind in {"number", "integer"}:
            try:
                if isinstance(value, bool):
                    raise ValueError
                number = float(value.strip().replace(",", ".")) if isinstance(value, str) else float(value)
                if not math.isfinite(number) or (kind == "integer" and not number.is_integer()):
                    raise ValueError
                if isinstance(value, str):
                    changes.append(f"{label} · строка приведена к числу")
                value = int(number) if kind == "integer" else number
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"«{label}» требует {'целое число' if kind == 'integer' else 'конечное число'}.") from exc
        elif kind == "string" and isinstance(value, str):
            value = value.strip()
        elif kind == "boolean" and isinstance(value, str):
            boolean = {"true": True, "false": False, "да": True, "нет": False}
            if value.strip().casefold() not in boolean:
                raise ValueError(f"«{label}» требует логическое значение.")
            value = boolean[value.strip().casefold()]
            changes.append(f"{label} · строка приведена к логическому значению")
        elif kind in {"object", "array"} and isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"«{label}» содержит некорректный JSON.") from exc
            if not isinstance(value, dict if kind == "object" else list):
                raise ValueError(f"«{label}» требует JSON-{'объект' if kind == 'object' else 'массив'}.")
        arguments[key] = value
    return arguments, changes
