"""Load the fail-closed content creation contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal

Cardinality = Literal["single", "multi"]


@dataclass(frozen=True)
class FieldRule:
    path: str
    component: str
    required: bool
    writable: bool
    cardinality: Cardinality | None = None
    collection_id: str | None = None
    options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ConditionalRequirement:
    path: str
    equals: Any
    require: tuple[str, ...]


@dataclass(frozen=True)
class ContentCreateSchema:
    content_type: str
    commit_ready: bool
    missing_contract: tuple[str, ...]
    fields: dict[str, FieldRule]
    conditional_requirements: tuple[ConditionalRequirement, ...]


@dataclass(frozen=True)
class CreateSchemaRegistry:
    version: str
    content_types: dict[str, ContentCreateSchema]


def load_create_schema(path: str | None) -> CreateSchemaRegistry:
    """Load an explicit creation schema or the packaged fail-closed default."""
    if path:
        schema_path = Path(path).expanduser()
        if not schema_path.is_file():
            raise FileNotFoundError(f"KMS creation schema not found at {schema_path}")
        raw = json.loads(schema_path.read_text(encoding="utf-8"))
    else:
        resource = resources.files("dots_kms_mcp.data").joinpath(
            "kms_create_schema.default.json"
        )
        raw = json.loads(resource.read_text(encoding="utf-8"))
    return _parse_registry(raw)


def _parse_registry(raw: Any) -> CreateSchemaRegistry:
    if not isinstance(raw, dict):
        raise ValueError("KMS creation schema must be a JSON object")

    version = raw.get("version")
    content_types_raw = raw.get("content_types")
    if not isinstance(version, str) or not version:
        raise ValueError("KMS creation schema version must be a non-empty string")
    if not isinstance(content_types_raw, dict):
        raise ValueError("KMS creation schema content_types must be an object")

    content_types: dict[str, ContentCreateSchema] = {}
    for content_type, schema_raw in content_types_raw.items():
        if not isinstance(content_type, str) or not isinstance(schema_raw, dict):
            raise ValueError("Each content type must map a string id to an object")

        commit_ready = schema_raw.get("commit_ready")
        missing_contract_raw = schema_raw.get("missing_contract")
        fields_raw = schema_raw.get("fields")
        conditionals_raw = schema_raw.get("conditional_requirements")
        if not isinstance(commit_ready, bool):
            raise ValueError(f"{content_type}.commit_ready must be a boolean")
        if not _is_string_list(missing_contract_raw):
            raise ValueError(f"{content_type}.missing_contract must be a string array")
        if commit_ready and missing_contract_raw:
            raise ValueError(
                f"{content_type}.commit_ready requires an empty missing_contract"
            )
        if not isinstance(fields_raw, dict):
            raise ValueError(f"{content_type}.fields must be an object")
        if not isinstance(conditionals_raw, list):
            raise ValueError(
                f"{content_type}.conditional_requirements must be an array"
            )

        fields = {
            field_path: _parse_field(content_type, field_path, field_raw)
            for field_path, field_raw in fields_raw.items()
        }
        title = fields.get("main.title")
        if title is None or title.component != "TitleInput" or not title.required:
            raise ValueError(
                f"{content_type}.main.title must be a required TitleInput"
            )
        if commit_ready:
            for field in fields.values():
                if field.component in ("RadioList", "CheckboxList") and not field.options:
                    raise ValueError(
                        f"{content_type}.{field.path}.options must be complete "
                        "when commit_ready"
                    )

        conditionals = tuple(
            _parse_conditional(content_type, index, conditional_raw)
            for index, conditional_raw in enumerate(conditionals_raw)
        )
        for conditional in conditionals:
            _require_writable_conditional_field(
                content_type, conditional.path, fields
            )
            for required_path in conditional.require:
                _require_writable_conditional_field(
                    content_type, required_path, fields
                )
        content_types[content_type] = ContentCreateSchema(
            content_type=content_type,
            commit_ready=commit_ready,
            missing_contract=tuple(missing_contract_raw),
            fields=fields,
            conditional_requirements=conditionals,
        )

    return CreateSchemaRegistry(version=version, content_types=content_types)


def _parse_field(content_type: str, path: Any, raw: Any) -> FieldRule:
    if not isinstance(path, str) or not isinstance(raw, dict):
        raise ValueError(f"{content_type} field rules must map string paths to objects")
    if not path.startswith(("main.", "tags.")):
        raise ValueError(
            f"{content_type}.{path} must be within main.* or tags.*"
        )

    component = raw.get("component")
    required = raw.get("required")
    writable = raw.get("writable")
    cardinality = raw.get("cardinality")
    collection_id = raw.get("collection_id")
    options_raw = raw.get("options", [])
    prefix = f"{content_type}.{path}"
    if not isinstance(component, str) or not component:
        raise ValueError(f"{prefix}.component must be a non-empty string")
    if not isinstance(required, bool):
        raise ValueError(f"{prefix}.required must be a boolean")
    if not isinstance(writable, bool):
        raise ValueError(f"{prefix}.writable must be a boolean")
    if cardinality not in (None, "single", "multi"):
        raise ValueError(f"{prefix}.cardinality must be single or multi")
    if collection_id is not None and not isinstance(collection_id, str):
        raise ValueError(f"{prefix}.collection_id must be a string")
    if not isinstance(options_raw, list):
        raise ValueError(f"{prefix}.options must be an array")

    options: list[tuple[str, str]] = []
    for option in options_raw:
        if not isinstance(option, dict):
            raise ValueError(f"{prefix}.options entries must be objects")
        value = option.get("value")
        display = option.get("display")
        if not isinstance(value, str) or not isinstance(display, str):
            raise ValueError(
                f"{prefix}.options entries require string value and display"
            )
        options.append((value, display))

    return FieldRule(
        path=path,
        component=component,
        required=required,
        writable=writable,
        cardinality=cardinality,
        collection_id=collection_id,
        options=tuple(options),
    )


def _parse_conditional(
    content_type: str, index: int, raw: Any
) -> ConditionalRequirement:
    prefix = f"{content_type}.conditional_requirements[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{prefix} must be an object")
    path = raw.get("path")
    require = raw.get("require")
    if not isinstance(path, str) or "equals" not in raw or not _is_string_list(require):
        raise ValueError(f"{prefix} requires path, equals, and a string require array")
    return ConditionalRequirement(path=path, equals=raw["equals"], require=tuple(require))


def _require_writable_conditional_field(
    content_type: str, path: str, fields: dict[str, FieldRule]
) -> None:
    field = fields.get(path)
    if field is None:
        raise ValueError(
            f"{content_type} conditional path {path} must reference a configured field"
        )
    if not field.writable:
        raise ValueError(
            f"{content_type} conditional path {path} must reference a writable field"
        )


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
