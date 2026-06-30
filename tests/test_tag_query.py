"""Tests for findQuery-based tag/date filtering helpers.

Tag filtering on the live API works via findQuery on the embedded
``tags.<collection>.data.<field>`` fields (NOT activeFilters). These helpers turn
human-friendly tag inputs into the right findQuery fragments.
"""

from __future__ import annotations

from dots_kms_mcp.configs import date_query, merge_find_query, tag_query

SCHEMA = {
    "tag_types": [
        {"id": "country", "filter_field": "tagId",
         "values": {"India": "india", "Indonesia": "indonesia"}},
        {"id": "states", "filter_field": "tagId",
         "values": {"East Java": "east_java"}},
        {"id": "nooraUsers", "filter_field": "_id",
         "values": {"Aarushi Surana": "66ebe2051313b20c9f35c72b"}},
    ]
}


def test_tag_query_resolves_display_name_to_tagid():
    assert tag_query({"country": ["India"]}, SCHEMA) == {
        "tags.country.data.tagId": {"$in": ["india"]}
    }


def test_tag_query_accepts_a_known_slug_verbatim():
    assert tag_query({"country": ["india"]}, SCHEMA) == {
        "tags.country.data.tagId": {"$in": ["india"]}
    }


def test_tag_query_is_case_insensitive_on_display_names():
    assert tag_query({"country": ["INDIA"]}, SCHEMA) == {
        "tags.country.data.tagId": {"$in": ["india"]}
    }


def test_tag_query_multiple_values_use_in():
    assert tag_query({"country": ["India", "Indonesia"]}, SCHEMA) == {
        "tags.country.data.tagId": {"$in": ["india", "indonesia"]}
    }


def test_tag_query_multiple_collections_are_anded():
    assert tag_query({"country": ["India"], "states": ["East Java"]}, SCHEMA) == {
        "tags.country.data.tagId": {"$in": ["india"]},
        "tags.states.data.tagId": {"$in": ["east_java"]},
    }


def test_tag_query_uses_id_field_for_slugless_collection():
    assert tag_query({"nooraUsers": ["Aarushi Surana"]}, SCHEMA) == {
        "tags.nooraUsers.data._id": {"$in": ["66ebe2051313b20c9f35c72b"]}
    }


def test_tag_query_falls_back_to_display_for_unknown_value():
    assert tag_query({"country": ["Atlantis"]}, SCHEMA) == {
        "tags.country.data.display": {"$in": ["Atlantis"]}
    }


def test_tag_query_mixes_resolved_and_unresolved_with_or():
    q = tag_query({"country": ["India", "Atlantis"]}, SCHEMA)
    assert q == {
        "$or": [
            {"tags.country.data.tagId": {"$in": ["india"]}},
            {"tags.country.data.display": {"$in": ["Atlantis"]}},
        ]
    }


def test_tag_query_empty_inputs_return_empty():
    assert tag_query({}, SCHEMA) == {}
    assert tag_query({"country": []}, SCHEMA) == {}


def test_tag_query_unknown_collection_matches_on_display():
    # No schema entry => default to matching the human value on display.
    assert tag_query({"mystery": ["Foo"]}, SCHEMA) == {
        "tags.mystery.data.display": {"$in": ["Foo"]}
    }


def test_date_query_start_and_end():
    assert date_query("kp_date_published", "2024-01-01", "2024-12-31") == {
        "kp_date_published": {"$gte": "2024-01-01", "$lte": "2024-12-31"}
    }


def test_date_query_only_start():
    assert date_query("d", start="2024-01-01") == {"d": {"$gte": "2024-01-01"}}


def test_date_query_no_bounds_is_empty():
    assert date_query("d") == {}


def test_merge_find_query_disjoint_keys_merge():
    assert merge_find_query({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


def test_merge_find_query_key_collision_uses_and():
    assert merge_find_query({"a": 1}, {"a": 2}) == {"$and": [{"a": 1}, {"a": 2}]}


def test_merge_find_query_handles_empty_sides():
    assert merge_find_query({}, {"a": 1}) == {"a": 1}
    assert merge_find_query({"a": 1}, None) == {"a": 1}
