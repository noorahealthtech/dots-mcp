"""Tests for the mock client (used when no creds are present)."""

from __future__ import annotations

import pytest

from dots_kms_mcp.errors import KmsConfigError
from dots_kms_mcp.mock_client import MockKmsClient


async def test_returns_data_and_count():
    client = MockKmsClient()
    result = await client.get_data({"contentTypes": ["articles"], "limit": 10, "countData": True})
    assert isinstance(result["data"], list)
    assert len(result["data"]) == 10
    assert result["count"] == 47
    doc = result["data"][0]
    assert doc["_id"] and len(doc["_id"]) == 24
    assert "title" in doc["meta"]


async def test_pagination_skip_advances_and_drops_on_last_page():
    client = MockKmsClient()
    first = await client.get_data({"contentTypes": ["articles"], "limit": 20, "skip": 0})
    assert first["skip"] == 20
    last = await client.get_data({"contentTypes": ["articles"], "limit": 20, "skip": 40})
    assert len(last["data"]) == 7  # 47 - 40
    assert "skip" not in last  # no more pages


async def test_count_false_omits_count():
    client = MockKmsClient()
    result = await client.get_data({"contentTypes": ["articles"], "limit": 5, "countData": False})
    assert "count" not in result


async def test_content_vs_profile_changes_pool():
    client = MockKmsClient()
    articles = await client.get_data({"contentTypes": ["articles"], "limit": 1})
    vols = await client.get_data({"profileTypes": ["volunteers"], "limit": 1})
    assert "articles" in articles["data"][0]["meta"]["title"]
    assert "volunteers" in vols["data"][0]["meta"]["title"]


async def test_search_term_folded_into_titles():
    client = MockKmsClient()
    result = await client.get_data(
        {"contentTypes": ["articles"], "searchTerm": "breastfeeding", "limit": 3}
    )
    assert all("Breastfeeding" in d["meta"]["title"] for d in result["data"])


async def test_deterministic_ids():
    client = MockKmsClient()
    a = await client.get_data({"contentTypes": ["articles"], "limit": 3})
    b = await client.get_data({"contentTypes": ["articles"], "limit": 3})
    assert [d["_id"] for d in a["data"]] == [d["_id"] for d in b["data"]]


async def test_tag_findquery_filters_documents():
    client = MockKmsClient()
    result = await client.get_data({
        "contentTypes": ["articles"],
        "findQuery": {"tags.states.data.tagId": {"$in": ["karnataka"]}},
        "limit": 50, "countData": True,
    })
    # 47 docs cycle through 3 states; "karnataka" hits indices where i % 3 == 0.
    assert result["count"] == 16
    assert result["data"]
    assert all(d["tags"]["states"]["data"][0]["tagId"] == "karnataka" for d in result["data"])


async def test_tag_findquery_count_only_respects_filter():
    client = MockKmsClient()
    result = await client.get_data({
        "contentTypes": ["articles"], "useCountDAL": True,
        "findQuery": {"tags.country.data.tagId": {"$in": ["india"]}},
    })
    assert result["count"] == 16  # india at i % 3 == 0


async def test_validation_still_fires():
    client = MockKmsClient()
    with pytest.raises(KmsConfigError):
        await client.get_data({})  # neither content nor profile types


async def test_mock_created_document_can_be_read_back():
    client = MockKmsClient()
    created = await client.create_and_publish(
        "routineVisits", {"main": {"title": "Mock visit"}}
    )
    doc_id = created["content"]["_id"]

    fetched = await client.get_data(
        {"contentTypes": ["routineVisits"], "findQuery": {"_id": doc_id}}
    )

    document = fetched["data"][0]
    assert document["main"]["title"] == "Mock visit"
    assert document["kp_published_status"] == "published"
    assert document["meta"]["kp_content_type"] == "routineVisits"
    assert document["meta"]["kp_contributed_by"]["name"] == "Mock Contributor"
    assert document["kp_date_created"]
    assert document["kp_date_published"]


async def test_mock_identical_creates_get_distinct_deterministic_ids():
    document = {
        "main": {"title": "Mock visit", "summary": "Same content"},
        "tags": {"country": []},
    }
    reordered_document = {
        "tags": {"country": []},
        "main": {"summary": "Same content", "title": "Mock visit"},
    }
    first_client = MockKmsClient()
    second_client = MockKmsClient()

    first_ids = [
        (await first_client.create_and_publish("routineVisits", document))["content"][
            "_id"
        ]
        for _ in range(2)
    ]
    second_ids = [
        (
            await second_client.create_and_publish(
                "routineVisits", reordered_document
            )
        )["content"]["_id"]
        for _ in range(2)
    ]

    assert first_ids[0] != first_ids[1]
    assert first_ids == second_ids


async def test_mock_created_documents_are_prepended_to_matching_listings():
    client = MockKmsClient()
    created = await client.create_and_publish(
        "routineVisits", {"main": {"title": "Newest visit"}}
    )

    listing = await client.get_data(
        {"contentTypes": ["routineVisits"], "limit": 2, "countData": True}
    )

    assert listing["data"][0]["_id"] == created["content"]["_id"]
    assert listing["count"] == 48
    assert listing["skip"] == 2


async def test_mock_created_documents_are_isolated_by_content_type():
    client = MockKmsClient()
    created = await client.create_and_publish(
        "routineVisits", {"main": {"title": "Mock visit"}}
    )
    doc_id = created["content"]["_id"]

    fetched = await client.get_data(
        {"contentTypes": ["articles"], "findQuery": {"_id": doc_id}}
    )

    assert fetched["data"][0]["meta"]["title"] != "Mock visit"
    assert fetched["data"][0]["metadata"]["contentType"] == "articles"


async def test_mock_create_does_not_mutate_or_retain_the_caller_document():
    client = MockKmsClient()
    document = {"main": {"title": "Original title"}}

    created = await client.create_and_publish("routineVisits", document)
    document["main"]["title"] = "Changed later"

    assert "_id" not in document
    assert created["content"]["main"]["title"] == "Original title"
