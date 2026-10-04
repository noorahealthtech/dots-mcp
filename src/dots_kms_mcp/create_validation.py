"""Validate content creation payloads against the explicit creation registry."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlparse

from .create_schema import ContentCreateSchema, CreateSchemaRegistry, FieldRule
from .getdata_client import KmsClientProtocol

MEDIA_REQUIRED_KEYS = {
    "name",
    "bucket",
    "contentType",
    "size",
    "publicUrl",
    "originalFilename",
    "mediaHost",
}


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    commit_ready: bool
    document: dict[str, Any]
    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]


def _issue(path: str, code: str, message: str) -> ValidationIssue:
    return ValidationIssue(path=path, code=code, message=message)


def _field_values(document: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for prefix in ("main", "tags"):
        section = document.get(prefix)
        if section is None:
            continue
        if not isinstance(section, dict):
            values[prefix] = section
            continue
        for key, value in section.items():
            values[f"{prefix}.{key}"] = value
    for key, value in document.items():
        if key not in {"main", "tags"}:
            values[key] = value
    return values


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (list, dict, tuple)) and not value


def _valid_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _validate_text(path: str, value: Any, rule: FieldRule) -> list[ValidationIssue]:
    if isinstance(value, str):
        return []
    return [_issue(path, "invalid_text", f"{rule.component} requires a string")]


def _validate_url(path: str, value: Any, rule: FieldRule) -> list[ValidationIssue]:
    if _valid_url(value):
        return []
    return [_issue(path, "invalid_url", "URLInput requires an http or https URL")]


def _validate_number(path: str, value: Any, rule: FieldRule) -> list[ValidationIssue]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return []
    return [_issue(path, "invalid_number", "NumberInput requires a JSON number")]


def _validate_date(path: str, value: Any, rule: FieldRule) -> list[ValidationIssue]:
    if _parse_date(value) is not None:
        return []
    return [
        _issue(
            path,
            "invalid_date",
            "DatePicker requires a timezone-bearing ISO 8601 date-time",
        )
    ]


def _validate_date_range(
    path: str, value: Any, rule: FieldRule
) -> list[ValidationIssue]:
    if not isinstance(value, list) or len(value) != 2:
        return [
            _issue(
                path,
                "invalid_date_range",
                "DateRangePicker requires exactly two date-times",
            )
        ]
    start, end = (_parse_date(item) for item in value)
    if start is None or end is None:
        return [
            _issue(
                path,
                "invalid_date_range",
                "DateRangePicker requires timezone-bearing ISO 8601 date-times",
            )
        ]
    if start > end:
        return [
            _issue(
                path,
                "reversed_date_range",
                "DateRangePicker start must not be after end",
            )
        ]
    return []


def _choice_item(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    choice_value = value.get("value")
    display = value.get("display")
    if not isinstance(choice_value, str) or not isinstance(display, str):
        return None
    return choice_value, display


def _validate_choices(
    path: str, items: list[Any], rule: FieldRule
) -> list[ValidationIssue]:
    parsed = [_choice_item(item) for item in items]
    if any(item is None for item in parsed):
        return [
            _issue(
                path,
                "invalid_choice_shape",
                f"{rule.component} choices require string value and display",
            )
        ]
    if not rule.options:
        return [
            _issue(
                path,
                "choice_options_unconfigured",
                f"Legal options for {rule.component} are not configured",
            )
        ]
    if any(item not in rule.options for item in parsed):
        return [
            _issue(path, "unknown_choice", f"Value is not legal for {rule.component}")
        ]
    return []


def _validate_radio(path: str, value: Any, rule: FieldRule) -> list[ValidationIssue]:
    if not isinstance(value, dict):
        return [
            _issue(path, "invalid_choice_shape", "RadioList requires one choice object")
        ]
    return _validate_choices(path, [value], rule)


def _validate_checkbox(
    path: str, value: Any, rule: FieldRule
) -> list[ValidationIssue]:
    if not isinstance(value, list):
        return [
            _issue(
                path,
                "invalid_choice_shape",
                "CheckboxList requires an array of choice objects",
            )
        ]
    return _validate_choices(path, value, rule)


def _validate_tag(path: str, value: Any, rule: FieldRule) -> list[ValidationIssue]:
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        return [_issue(path, "invalid_tag", f"{rule.component} requires a tag object")]
    if value.get("collectionId") != rule.collection_id:
        return [
            _issue(
                path,
                "tag_collection_mismatch",
                f"collectionId must be {rule.collection_id}",
            )
        ]
    data = value["data"]
    if rule.cardinality == "single" and len(data) != 1:
        return [
            _issue(path, "invalid_tag_count", "TagsInputSingle requires one tag")
        ]
    issues: list[ValidationIssue] = []
    for index, item in enumerate(data):
        item_path = f"{path}.data[{index}]"
        if not isinstance(item, dict) or any(
            not isinstance(item.get(key), str) or not item[key]
            for key in ("_id", "display", "tagId")
        ):
            issues.append(
                _issue(
                    item_path,
                    "invalid_tag",
                    "Tag requires non-empty _id, display, and tagId strings",
                )
            )
    return issues


def _collect_lexical_text(node: Any) -> str | None:
    if not isinstance(node, dict):
        return None
    if "text" in node:
        return node["text"] if isinstance(node["text"], str) else None
    children = node.get("children", [])
    if not isinstance(children, list):
        return None
    parts: list[str] = []
    for child in children:
        text = _collect_lexical_text(child)
        if text is None:
            return None
        parts.append(text)
    return "".join(parts)


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _validate_rich_text(
    path: str, value: Any, rule: FieldRule
) -> list[ValidationIssue]:
    if (
        not isinstance(value, dict)
        or value.get("isLexical") is not True
        or not isinstance(value.get("allText"), str)
        or not isinstance(value.get("value"), dict)
    ):
        return [
            _issue(
                path,
                "invalid_rich_text",
                "LexicalTextEditor requires isLexical, allText, and value",
            )
        ]
    root = value["value"].get("root")
    if not isinstance(root, dict) or not isinstance(root.get("children"), list):
        return [_issue(path, "invalid_lexical_root", "Lexical root is malformed")]
    blocks: list[str] = []
    for child in root["children"]:
        text = _collect_lexical_text(child)
        if text is None:
            return [_issue(path, "invalid_lexical_root", "Lexical root is malformed")]
        blocks.append(text)
    extracted = _normalize_text(" ".join(blocks))
    if extracted != _normalize_text(value["allText"]):
        return [
            _issue(
                path,
                "rich_text_mismatch",
                "allText must agree with the Lexical editor text",
            )
        ]
    return []


def _validate_media(path: str, value: Any, rule: FieldRule) -> list[ValidationIssue]:
    if not isinstance(value, list):
        return [_issue(path, "invalid_media", f"{rule.component} requires an array")]
    issues: list[ValidationIssue] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not MEDIA_REQUIRED_KEYS.issubset(item):
            issues.append(
                _issue(
                    f"{path}[{index}]",
                    "invalid_media",
                    "Media entry is missing successful-upload fields",
                )
            )
    return issues


def _validate_link(path: str, value: Any, rule: FieldRule) -> list[ValidationIssue]:
    if isinstance(value, dict) and _valid_url(value.get("url")):
        return []
    return [
        _issue(
            path,
            "invalid_link",
            "LinkEmbedWithInput requires an object with an http or https url",
        )
    ]


def _validate_non_writable(
    path: str, value: Any, rule: FieldRule
) -> list[ValidationIssue]:
    return [_issue(path, "not_writable", f"{rule.component} cannot be supplied")]


ComponentValidator = Callable[[str, Any, FieldRule], list[ValidationIssue]]

COMPONENT_VALIDATORS: dict[str, ComponentValidator] = {
    "TitleInput": _validate_text,
    "TextInput": _validate_text,
    "SummaryInput": _validate_text,
    "URLInput": _validate_url,
    "NumberInput": _validate_number,
    "DatePicker": _validate_date,
    "DateRangePicker": _validate_date_range,
    "RadioList": _validate_radio,
    "CheckboxList": _validate_checkbox,
    "TagsInputSingle": _validate_tag,
    "TagsInputMulti": _validate_tag,
    "LexicalTextEditor": _validate_rich_text,
    "PDFInput": _validate_media,
    "ImageInput": _validate_media,
    "LinkEmbedWithInput": _validate_link,
    "StaticRichText": _validate_non_writable,
    "Repeater": _validate_non_writable,
}


def _schema_coverage_issue(schema: ContentCreateSchema) -> ValidationIssue:
    missing = ", ".join(schema.missing_contract) or "unspecified contract details"
    return _issue(
        "content_type",
        "schema_incomplete",
        f"{schema.content_type} is not commit-ready: {missing}",
    )


async def _verify_tags(
    references: dict[str, list[tuple[str, dict[str, Any]]]],
    client: KmsClientProtocol,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for collection_id, collection_references in references.items():
        ids = list(dict.fromkeys(item["_id"] for _, item in collection_references))
        response = await client.get_data(
            {
                "contentTypes": [collection_id],
                "findQuery": {"_id": {"$in": ids}},
                "projection": {"_id": 1, "main.title": 1, "tagId": 1},
                "limit": len(ids),
                "countData": False,
            }
        )
        documents = response.get("data") if isinstance(response, dict) else None
        by_id = {
            document.get("_id"): document
            for document in documents or []
            if isinstance(document, dict) and isinstance(document.get("_id"), str)
        }
        for path, requested in collection_references:
            stored = by_id.get(requested["_id"])
            if stored is None:
                issues.append(
                    _issue(path, "tag_not_found", "Referenced tag does not exist")
                )
                continue
            main = stored.get("main")
            stored_title = main.get("title") if isinstance(main, dict) else None
            if stored_title != requested["display"]:
                issues.append(
                    _issue(
                        path,
                        "tag_display_mismatch",
                        "Tag display does not match the stored title",
                    )
                )
            if stored.get("tagId") != requested["tagId"]:
                issues.append(
                    _issue(
                        path,
                        "tag_id_mismatch",
                        "Tag tagId does not match the stored tagId",
                    )
                )
    return issues


async def validate_create_document(
    content_type: str,
    document: dict[str, Any],
    registry: CreateSchemaRegistry,
    client: KmsClientProtocol,
    require_commit_ready: bool = False,
) -> ValidationResult:
    """Validate and verify a candidate without mutating caller-owned data."""
    candidate = copy.deepcopy(document)
    schema = registry.content_types.get(content_type)
    if schema is None:
        errors = (
            _issue(
                "content_type",
                "unknown_content_type",
                f"No creation schema is configured for {content_type}",
            ),
        )
        return ValidationResult(False, False, candidate, errors, ())

    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    if not schema.commit_ready:
        coverage_issue = _schema_coverage_issue(schema)
        if require_commit_ready:
            errors.append(coverage_issue)
        else:
            warnings.append(coverage_issue)

    values = _field_values(candidate)
    tag_references: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for path, value in values.items():
        rule = schema.fields.get(path)
        if rule is None:
            errors.append(_issue(path, "unknown_path", "Path is not writable"))
            continue
        if not rule.writable:
            errors.append(_issue(path, "not_writable", f"{rule.component} cannot be supplied"))
            continue
        validator = COMPONENT_VALIDATORS.get(rule.component)
        if validator is None:
            errors.append(
                _issue(
                    path,
                    "unsupported_component",
                    f"No validator exists for {rule.component}",
                )
            )
            continue
        component_issues = validator(path, value, rule)
        errors.extend(component_issues)
        if rule.component in {"TagsInputSingle", "TagsInputMulti"} and not component_issues:
            for index, item in enumerate(value["data"]):
                tag_references.setdefault(rule.collection_id or "", []).append(
                    (f"{path}.data[{index}]", item)
                )

    for path, rule in schema.fields.items():
        if rule.required and (path not in values or _missing(values[path])):
            errors.append(_issue(path, "required", "Required field is missing or empty"))

    for requirement in schema.conditional_requirements:
        if values.get(requirement.path) != requirement.equals:
            continue
        for required_path in requirement.require:
            if required_path not in values or _missing(values[required_path]):
                errors.append(
                    _issue(
                        required_path,
                        "conditionally_required",
                        f"Field is required when {requirement.path} matches",
                    )
                )

    errors.extend(await _verify_tags(tag_references, client))
    valid = not errors
    return ValidationResult(
        valid=valid,
        commit_ready=valid and schema.commit_ready,
        document=candidate,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
