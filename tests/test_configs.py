"""Tests for the pure configs builder + validator."""

from __future__ import annotations

import pytest

from dots_kms_mcp.configs import build_configs, validate_configs
from dots_kms_mcp.errors import KmsConfigError


def test_maps_friendly_kwargs_to_api_names():
    configs = build_configs(
        content_types=["articles"],
        search_term="climate",
        filters=[{"target": {"filterType": "tagType", "tagType": "c"}, "values": ["x"]}],
        find_query={"status": "published"},
        sort={"createdAt": -1},
        projection={"meta.title": 1},
        limit=20,
        skip=10,
        count=True,
    )
    assert configs["contentTypes"] == ["articles"]
    assert configs["searchTerm"] == "climate"
    assert configs["activeFilters"][0]["target"]["filterType"] == "tagType"
    assert configs["findQuery"] == {"status": "published"}
    assert configs["activeSort"] == {"createdAt": -1}
    assert configs["projection"] == {"meta.title": 1}
    assert configs["limit"] == 20
    assert configs["skip"] == 10
    assert configs["countData"] is True


def test_omits_none_fields():
    configs = build_configs(content_types=["articles"])
    # Only contentTypes, skip(default 0), countData(default True) should be present.
    assert set(configs) == {"contentTypes", "skip", "countData"}
    assert "searchTerm" not in configs
    assert "limit" not in configs  # default "no limit" => field absent


def test_defaults_skip_and_count():
    configs = build_configs(profile_types=["volunteers"])
    assert configs["skip"] == 0
    assert configs["countData"] is True


def test_count_false_still_present_as_false():
    configs = build_configs(content_types=["articles"], count=False)
    assert configs["countData"] is False


def test_extra_passthrough_for_advanced_fields():
    configs = build_configs(
        content_types=["articles"],
        extra={"population": [{"path": "tags", "select": "name"}], "facet": None},
    )
    assert configs["population"] == [{"path": "tags", "select": "name"}]
    assert "facet" not in configs  # None values in extra are dropped


def test_validate_raises_when_neither_type():
    with pytest.raises(KmsConfigError, match="content type or profile type"):
        validate_configs({})


def test_validate_raises_when_both_types():
    with pytest.raises(KmsConfigError, match="cannot pass both"):
        validate_configs({"contentTypes": ["a"], "profileTypes": ["b"]})


def test_build_raises_when_both_types():
    with pytest.raises(KmsConfigError):
        build_configs(content_types=["a"], profile_types=["b"])
