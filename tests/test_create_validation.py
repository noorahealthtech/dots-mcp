import copy
import json
from dataclasses import replace

import pytest

from dots_kms_mcp.create_schema import (
    ConditionalRequirement,
    ContentCreateSchema,
    CreateSchemaRegistry,
    FieldRule,
)
from dots_kms_mcp.create_validation import validate_create_document


TAG_ID = "657663f98fe0ed6bdaf8db43"
COUNTRY_TAG = {
    "collectionId": "country",
    "data": [{"_id": TAG_ID, "display": "Bangladesh", "tagId": "bangladesh"}],
}
MEDIA = {
    "name": "visit.pdf",
    "bucket": "kms",
    "contentType": "application/pdf",
    "size": 123,
    "publicUrl": "https://storage.example/visit.pdf",
    "originalFilename": "visit.pdf",
    "mediaHost": "gcs",
    "uploadMetadata": {"preserve": True},
}


class TagClient:
    def __init__(self, documents=None):
        self.documents = documents or {
            "country": [
                {
                    "_id": TAG_ID,
                    "main": {"title": "Bangladesh"},
                    "tagId": "bangladesh",
                }
            ]
        }
        self.calls = []

    async def get_data(self, configs):
        self.calls.append(copy.deepcopy(configs))
        collection_id = configs["contentTypes"][0]
        requested = set(configs["findQuery"]["_id"]["$in"])
        return {
            "data": [
                document
                for document in self.documents.get(collection_id, [])
                if document.get("_id") in requested
            ]
        }


def _rule(
    path,
    component,
    *,
    required=False,
    writable=True,
    cardinality=None,
    collection_id=None,
    options=(),
):
    return FieldRule(
        path=path,
        component=component,
        required=required,
        writable=writable,
        cardinality=cardinality,
        collection_id=collection_id,
        options=options,
    )


@pytest.fixture
def complete_registry():
    fields = {
        "main.title": _rule("main.title", "TitleInput", required=True),
        "main.text": _rule("main.text", "TextInput"),
        "main.summary": _rule("main.summary", "SummaryInput"),
        "main.url": _rule("main.url", "URLInput"),
        "main.number": _rule("main.number", "NumberInput"),
        "main.date": _rule("main.date", "DatePicker"),
        "main.range": _rule("main.range", "DateRangePicker"),
        "main.radio": _rule(
            "main.radio",
            "RadioList",
            options=(("yes", "Yes"), ("details", "Needs details")),
        ),
        "main.checkbox": _rule(
            "main.checkbox",
            "CheckboxList",
            options=(("first", "First"), ("second", "Second")),
        ),
        "tags.country": _rule(
            "tags.country",
            "TagsInputSingle",
            cardinality="single",
            collection_id="country",
        ),
        "tags.subject": _rule(
            "tags.subject",
            "TagsInputMulti",
            cardinality="multi",
            collection_id="subject",
        ),
        "main.rich": _rule("main.rich", "LexicalTextEditor"),
        "main.pdf": _rule("main.pdf", "PDFInput"),
        "main.image": _rule("main.image", "ImageInput"),
        "main.link": _rule("main.link", "LinkEmbedWithInput"),
        "main.static": _rule("main.static", "StaticRichText", writable=False),
        "main.repeater": _rule("main.repeater", "Repeater", writable=False),
        "main.conditionalText": _rule("main.conditionalText", "TextInput"),
    }
    schema = ContentCreateSchema(
        content_type="routineVisits",
        commit_ready=True,
        missing_contract=(),
        fields=fields,
        conditional_requirements=(
            ConditionalRequirement(
                path="main.radio",
                equals={"value": "details", "display": "Needs details"},
                require=("main.conditionalText",),
            ),
        ),
    )
    return CreateSchemaRegistry(version="test", content_types={"routineVisits": schema})


@pytest.fixture
def registry(complete_registry):
    schema = complete_registry.content_types["routineVisits"]
    incomplete = replace(
        schema,
        commit_ready=False,
        missing_contract=("required_fields", "conditional_rules"),
    )
    return replace(complete_registry, content_types={"routineVisits": incomplete})


@pytest.fixture
def mock_client():
    return TagClient()


def _document(**main):
    return {"main": {"title": "Visit", **main}}


def _codes(result, path):
    return [issue.code for issue in result.errors if issue.path == path]


def _rich_text(all_text="Hello world"):
    return {
        "isLexical": True,
        "allText": all_text,
        "value": {
            "root": {
                "type": "root",
                "children": [
                    {
                        "type": "paragraph",
                        "children": [
                            {"type": "text", "text": "Hello"},
                            {
                                "type": "link",
                                "children": [{"type": "text", "text": " world"}],
                            },
                        ],
                    }
                ],
            }
        },
        "metadata": {"preserve": True},
    }


async def test_unknown_path_is_rejected(complete_registry, mock_client):
    result = await validate_create_document(
        "routineVisits",
        _document(notAField="bad"),
        complete_registry,
        mock_client,
    )
    assert [(e.path, e.code) for e in result.errors] == [
        ("main.notAField", "unknown_path")
    ]


async def test_commit_rejects_incomplete_registry(registry, mock_client):
    result = await validate_create_document(
        "routineVisits",
        _document(),
        registry,
        mock_client,
        require_commit_ready=True,
    )
    assert result.valid is False
    assert result.commit_ready is False
    assert any(e.code == "schema_incomplete" for e in result.errors)


async def test_preview_reports_incomplete_registry_as_warning(registry, mock_client):
    result = await validate_create_document(
        "routineVisits",
        _document(),
        registry,
        mock_client,
        require_commit_ready=False,
    )
    assert any(w.code == "schema_incomplete" for w in result.warnings)
    assert not any(e.code == "schema_incomplete" for e in result.errors)


async def test_valid_tag_is_verified_without_mutating_payload(
    complete_registry, mock_client
):
    country = copy.deepcopy(COUNTRY_TAG)
    document = _document()
    document["tags"] = {"country": country}
    result = await validate_create_document(
        "routineVisits", document, complete_registry, mock_client
    )
    assert result.errors == ()
    assert result.document["tags"]["country"] == country
    assert mock_client.calls == [
        {
            "contentTypes": ["country"],
            "findQuery": {"_id": {"$in": [TAG_ID]}},
            "projection": {"_id": 1, "main.title": 1, "tagId": 1},
            "limit": 1,
            "countData": False,
        }
    ]


@pytest.mark.parametrize("title", [None, "", "   "])
async def test_missing_or_empty_title_is_rejected(
    complete_registry, mock_client, title
):
    document = _document()
    if title is None:
        del document["main"]["title"]
    else:
        document["main"]["title"] = title
    result = await validate_create_document(
        "routineVisits", document, complete_registry, mock_client
    )
    assert "required" in _codes(result, "main.title")


@pytest.mark.parametrize(
    ("path", "value"), [("main.title", 12), ("main.text", ["not text"])]
)
async def test_text_and_title_require_strings(
    complete_registry, mock_client, path, value
):
    document = _document()
    document["main"][path.split(".")[1]] = value
    result = await validate_create_document(
        "routineVisits", document, complete_registry, mock_client
    )
    assert "invalid_text" in _codes(result, path)


@pytest.mark.parametrize("value", ["example.com", "ftp://example.com", "not a url"])
async def test_url_requires_http_or_https(complete_registry, mock_client, value):
    result = await validate_create_document(
        "routineVisits", _document(url=value), complete_registry, mock_client
    )
    assert "invalid_url" in _codes(result, "main.url")


@pytest.mark.parametrize("value", [True, False, "12", "1.5"])
async def test_number_rejects_booleans_and_numeric_strings(
    complete_registry, mock_client, value
):
    result = await validate_create_document(
        "routineVisits", _document(number=value), complete_registry, mock_client
    )
    assert "invalid_number" in _codes(result, "main.number")


@pytest.mark.parametrize("value", ["2026-10-05T12:00:00", "not-a-date", 123])
async def test_date_requires_well_formed_timezone_aware_iso_value(
    complete_registry, mock_client, value
):
    result = await validate_create_document(
        "routineVisits", _document(date=value), complete_registry, mock_client
    )
    assert "invalid_date" in _codes(result, "main.date")


@pytest.mark.parametrize(
    "value",
    [
        [],
        ["2026-10-05T12:00:00Z"],
        ["2026-10-05T12:00:00Z", "2026-10-06T12:00:00Z", "extra"],
    ],
)
async def test_date_range_requires_exactly_two_values(
    complete_registry, mock_client, value
):
    result = await validate_create_document(
        "routineVisits", _document(range=value), complete_registry, mock_client
    )
    assert "invalid_date_range" in _codes(result, "main.range")


async def test_date_range_rejects_reversed_bounds(complete_registry, mock_client):
    value = ["2026-10-06T12:00:00Z", "2026-10-05T12:00:00Z"]
    result = await validate_create_document(
        "routineVisits", _document(range=value), complete_registry, mock_client
    )
    assert "reversed_date_range" in _codes(result, "main.range")


@pytest.mark.parametrize(
    ("field", "value"),
    [("radio", [{"value": "yes", "display": "Yes"}]), ("checkbox", {"value": "first", "display": "First"})],
)
async def test_choice_components_require_their_documented_object_shape(
    complete_registry, mock_client, field, value
):
    result = await validate_create_document(
        "routineVisits", _document(**{field: value}), complete_registry, mock_client
    )
    assert "invalid_choice_shape" in _codes(result, f"main.{field}")


async def test_unconfigured_choice_values_fail_closed(
    complete_registry, mock_client
):
    schema = complete_registry.content_types["routineVisits"]
    fields = dict(schema.fields)
    fields["main.radio"] = replace(fields["main.radio"], options=())
    incomplete = replace(schema, commit_ready=False, missing_contract=("choice_options:main.radio",), fields=fields)
    registry = replace(complete_registry, content_types={"routineVisits": incomplete})
    result = await validate_create_document(
        "routineVisits",
        _document(radio={"value": "yes", "display": "Yes"}),
        registry,
        mock_client,
    )
    assert "choice_options_unconfigured" in _codes(result, "main.radio")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("radio", {"value": "unknown", "display": "Unknown"}),
        ("radio", {"value": "yes", "display": "Wrong"}),
        ("checkbox", [{"value": "unknown", "display": "Unknown"}]),
    ],
)
async def test_unknown_choice_values_are_rejected(
    complete_registry, mock_client, field, value
):
    result = await validate_create_document(
        "routineVisits", _document(**{field: value}), complete_registry, mock_client
    )
    assert "unknown_choice" in _codes(result, f"main.{field}")


@pytest.mark.parametrize("items", [[], [COUNTRY_TAG["data"][0], COUNTRY_TAG["data"][0]]])
async def test_single_tag_requires_exactly_one_item(
    complete_registry, mock_client, items
):
    document = _document()
    document["tags"] = {"country": {"collectionId": "country", "data": items}}
    result = await validate_create_document(
        "routineVisits", document, complete_registry, mock_client
    )
    assert "invalid_tag_count" in _codes(result, "tags.country")


async def test_tag_collection_must_match_registry(complete_registry, mock_client):
    country = copy.deepcopy(COUNTRY_TAG)
    country["collectionId"] = "states"
    document = _document()
    document["tags"] = {"country": country}
    result = await validate_create_document(
        "routineVisits", document, complete_registry, mock_client
    )
    assert "tag_collection_mismatch" in _codes(result, "tags.country")


@pytest.mark.parametrize("missing_key", ["_id", "display", "tagId"])
async def test_tag_items_require_complete_nonempty_triplet(
    complete_registry, mock_client, missing_key
):
    country = copy.deepcopy(COUNTRY_TAG)
    del country["data"][0][missing_key]
    document = _document()
    document["tags"] = {"country": country}
    result = await validate_create_document(
        "routineVisits", document, complete_registry, mock_client
    )
    assert "invalid_tag" in _codes(result, "tags.country.data[0]")


async def test_missing_referenced_tag_is_rejected(complete_registry):
    document = _document()
    document["tags"] = {"country": copy.deepcopy(COUNTRY_TAG)}
    result = await validate_create_document(
        "routineVisits", document, complete_registry, TagClient(documents={"country": []})
    )
    assert "tag_not_found" in _codes(result, "tags.country.data[0]")


@pytest.mark.parametrize(
    ("stored_title", "stored_tag_id", "code"),
    [
        ("India", "bangladesh", "tag_display_mismatch"),
        ("Bangladesh", "india", "tag_id_mismatch"),
    ],
)
async def test_stored_tag_identity_must_match_payload(
    complete_registry, stored_title, stored_tag_id, code
):
    client = TagClient(
        documents={
            "country": [
                {
                    "_id": TAG_ID,
                    "main": {"title": stored_title},
                    "tagId": stored_tag_id,
                }
            ]
        }
    )
    document = _document()
    document["tags"] = {"country": copy.deepcopy(COUNTRY_TAG)}
    result = await validate_create_document(
        "routineVisits", document, complete_registry, client
    )
    assert code in _codes(result, "tags.country.data[0]")


async def test_tag_lookups_are_grouped_once_per_collection(complete_registry):
    subject_ids = ["subject-1", "subject-2"]
    client = TagClient(
        documents={
            "country": [
                {"_id": TAG_ID, "main": {"title": "Bangladesh"}, "tagId": "bangladesh"}
            ],
            "subject": [
                {"_id": "subject-1", "main": {"title": "First"}, "tagId": "first"},
                {"_id": "subject-2", "main": {"title": "Second"}, "tagId": "second"},
            ],
        }
    )
    document = _document()
    document["tags"] = {
        "country": copy.deepcopy(COUNTRY_TAG),
        "subject": {
            "collectionId": "subject",
            "data": [
                {"_id": "subject-1", "display": "First", "tagId": "first"},
                {"_id": "subject-2", "display": "Second", "tagId": "second"},
            ],
        },
    }
    result = await validate_create_document(
        "routineVisits", document, complete_registry, client
    )
    assert result.errors == ()
    assert [call["contentTypes"] for call in client.calls] == [["country"], ["subject"]]
    assert client.calls[1]["findQuery"] == {"_id": {"$in": subject_ids}}


@pytest.mark.parametrize("missing_key", ["isLexical", "allText", "value"])
async def test_rich_text_requires_all_contract_keys(
    complete_registry, mock_client, missing_key
):
    rich = _rich_text()
    del rich[missing_key]
    result = await validate_create_document(
        "routineVisits", _document(rich=rich), complete_registry, mock_client
    )
    assert "invalid_rich_text" in _codes(result, "main.rich")


async def test_rich_text_requires_lexical_value(complete_registry, mock_client):
    rich = _rich_text()
    rich["isLexical"] = False
    result = await validate_create_document(
        "routineVisits", _document(rich=rich), complete_registry, mock_client
    )
    assert "invalid_rich_text" in _codes(result, "main.rich")


@pytest.mark.parametrize(
    "value", [{}, {"root": None}, {"root": {"children": "not-an-array"}}]
)
async def test_rich_text_requires_well_formed_lexical_root(
    complete_registry, mock_client, value
):
    rich = _rich_text()
    rich["value"] = value
    result = await validate_create_document(
        "routineVisits", _document(rich=rich), complete_registry, mock_client
    )
    assert "invalid_lexical_root" in _codes(result, "main.rich")


async def test_rich_text_all_text_must_agree_with_editor_text(
    complete_registry, mock_client
):
    result = await validate_create_document(
        "routineVisits",
        _document(rich=_rich_text("Different text")),
        complete_registry,
        mock_client,
    )
    assert "rich_text_mismatch" in _codes(result, "main.rich")


@pytest.mark.parametrize("field", ["pdf", "image"])
async def test_media_components_require_arrays(
    complete_registry, mock_client, field
):
    result = await validate_create_document(
        "routineVisits", _document(**{field: MEDIA}), complete_registry, mock_client
    )
    assert "invalid_media" in _codes(result, f"main.{field}")


@pytest.mark.parametrize("missing_key", sorted(MEDIA.keys() - {"uploadMetadata"}))
async def test_media_entries_require_successful_upload_core_fields(
    complete_registry, mock_client, missing_key
):
    media = copy.deepcopy(MEDIA)
    del media[missing_key]
    result = await validate_create_document(
        "routineVisits", _document(pdf=[media]), complete_registry, mock_client
    )
    assert "invalid_media" in _codes(result, "main.pdf[0]")


@pytest.mark.parametrize(
    "value", ["https://example.com", {}, {"url": "ftp://example.com"}, {"url": 12}]
)
async def test_link_embed_requires_object_with_http_url(
    complete_registry, mock_client, value
):
    result = await validate_create_document(
        "routineVisits", _document(link=value), complete_registry, mock_client
    )
    assert "invalid_link" in _codes(result, "main.link")


@pytest.mark.parametrize("field", ["static", "repeater"])
async def test_non_writable_components_are_rejected_when_supplied(
    complete_registry, mock_client, field
):
    result = await validate_create_document(
        "routineVisits", _document(**{field: {}}), complete_registry, mock_client
    )
    assert "not_writable" in _codes(result, f"main.{field}")


async def test_authoritative_required_fields_are_enforced(
    complete_registry, mock_client
):
    schema = complete_registry.content_types["routineVisits"]
    fields = dict(schema.fields)
    fields["main.requiredText"] = _rule(
        "main.requiredText", "TextInput", required=True
    )
    required_registry = replace(
        complete_registry,
        content_types={"routineVisits": replace(schema, fields=fields)},
    )
    result = await validate_create_document(
        "routineVisits",
        {"main": {"title": "Visit"}},
        required_registry,
        mock_client,
    )
    assert "required" in _codes(result, "main.requiredText")


async def test_conditional_requirements_are_enforced(
    complete_registry, mock_client
):
    result = await validate_create_document(
        "routineVisits",
        _document(radio={"value": "details", "display": "Needs details"}),
        complete_registry,
        mock_client,
    )
    assert "conditionally_required" in _codes(result, "main.conditionalText")


async def test_validation_preserves_complete_document_byte_for_byte(
    complete_registry, mock_client
):
    document = _document(
        text="Text",
        summary="Summary",
        url="https://example.com/path",
        number=12.5,
        date="2026-10-05T12:00:00Z",
        range=["2026-10-05T12:00:00Z", "2026-10-06T12:00:00+00:00"],
        radio={"value": "yes", "display": "Yes"},
        checkbox=[{"value": "first", "display": "First"}],
        rich=_rich_text("  Hello   world "),
        pdf=[copy.deepcopy(MEDIA)],
        image=[copy.deepcopy(MEDIA)],
        link={"url": "https://example.com", "label": "Preserve me"},
    )
    document["tags"] = {"country": copy.deepcopy(COUNTRY_TAG)}
    before = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
    result = await validate_create_document(
        "routineVisits", document, complete_registry, mock_client
    )
    after_input = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
    after_result = json.dumps(result.document, separators=(",", ":"), ensure_ascii=False)
    assert result.valid is True
    assert result.commit_ready is True
    assert result.errors == ()
    assert before == after_input == after_result


async def test_unknown_content_type_fails_closed(complete_registry, mock_client):
    result = await validate_create_document(
        "unknown", _document(), complete_registry, mock_client
    )
    assert result.valid is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("content_type", "unknown_content_type")
    ]
