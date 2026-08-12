"""Validation for the deliberately small app-settings JSON Schema subset."""

from __future__ import annotations

import re
from typing import Any


class SettingsSchemaError(ValueError):
    pass


_TYPES = frozenset({"object", "string", "integer", "boolean", "array"})
_KEYS = frozenset(
    {
        "type", "properties", "required", "additionalProperties", "enum",
        "minLength", "maxLength", "minimum", "maximum", "items", "maxItems",
        "uniqueItems", "pattern", "default",
    }
)


def validate_schema(schema: dict[str, Any], *, path: str = "$settings") -> None:
    if not isinstance(schema, dict):
        raise SettingsSchemaError(f"{path} must be an object")
    unknown = set(schema) - _KEYS
    if unknown:
        raise SettingsSchemaError(f"{path} has unsupported keywords: {sorted(unknown)}")
    schema_type = schema.get("type")
    if schema_type not in _TYPES:
        raise SettingsSchemaError(f"{path}.type is unsupported")
    if schema_type == "object":
        if schema.get("additionalProperties", False) is not False:
            raise SettingsSchemaError(f"{path}.additionalProperties must be false")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict) or not all(isinstance(k, str) for k in properties):
            raise SettingsSchemaError(f"{path}.properties must be an object")
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(k, str) for k in required):
            raise SettingsSchemaError(f"{path}.required must be a string array")
        if not set(required).issubset(properties):
            raise SettingsSchemaError(f"{path}.required contains an unknown property")
        for key, child in properties.items():
            validate_schema(child, path=f"{path}.{key}")
    elif schema_type == "array":
        if "items" not in schema:
            raise SettingsSchemaError(f"{path}.items is required")
        validate_schema(schema["items"], path=f"{path}[]")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise SettingsSchemaError(f"{path}.enum must be a non-empty array")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (TypeError, re.error) as error:
            raise SettingsSchemaError(f"{path}.pattern is invalid") from error


def validate_settings(schema: dict[str, Any], value: Any, *, path: str = "$settings") -> None:
    validate_schema(schema, path=path)
    schema_type = schema["type"]
    valid_type = {
        "object": lambda item: isinstance(item, dict),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "array": lambda item: isinstance(item, list),
    }[schema_type]
    if not valid_type(value):
        raise SettingsSchemaError(f"{path} must be {schema_type}")
    if "enum" in schema and value not in schema["enum"]:
        raise SettingsSchemaError(f"{path} is not an allowed value")
    if schema_type == "object":
        properties = schema.get("properties", {})
        unknown = set(value) - set(properties)
        if unknown:
            raise SettingsSchemaError(f"{path} has unknown properties: {sorted(unknown)}")
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise SettingsSchemaError(f"{path} is missing required properties: {sorted(missing)}")
        for key, item in value.items():
            validate_settings(properties[key], item, path=f"{path}.{key}")
    elif schema_type == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 2**31):
            raise SettingsSchemaError(f"{path} length is outside the allowed range")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise SettingsSchemaError(f"{path} does not match the required pattern")
    elif schema_type == "integer":
        if value < schema.get("minimum", -(2**63)) or value > schema.get("maximum", 2**63 - 1):
            raise SettingsSchemaError(f"{path} is outside the allowed range")
    elif schema_type == "array":
        if len(value) > schema.get("maxItems", 100):
            raise SettingsSchemaError(f"{path} has too many items")
        if schema.get("uniqueItems") and len({repr(item) for item in value}) != len(value):
            raise SettingsSchemaError(f"{path} must contain unique items")
        for index, item in enumerate(value):
            validate_settings(schema["items"], item, path=f"{path}[{index}]")
