from dots_kms_mcp.create_schema import load_create_schema


def test_packaged_create_schema_contains_documented_contract():
    registry = load_create_schema(None)
    assert set(registry.content_types) == {
        "learningAndSharingSessions",
        "organisationalReports",
        "programmaticAssetsTemplates",
        "programPerformanceReports",
        "reports",
        "researchAndEvaluationReports",
        "routineVisits",
        "successStory",
        "toolsAndCollaterals",
        "trainingReports",
    }
    assert sum(len(schema.fields) for schema in registry.content_types.values()) == 159
    routine = registry.content_types["routineVisits"]
    assert routine.fields["main.date"].component == "DatePicker"
    assert routine.fields["tags.country"].collection_id == "country"
    assert routine.fields["tags.country"].cardinality == "single"


def test_non_input_components_are_explicitly_not_writable():
    registry = load_create_schema(None)
    static = registry.content_types["researchAndEvaluationReports"].fields[
        "main.enterTheTitleOfTheFinalDocumentManuscript"
    ]
    repeater = registry.content_types["successStory"].fields["main.testimonials"]
    assert static.writable is False
    assert repeater.writable is False


def test_packaged_registry_fails_closed_until_contract_is_complete():
    registry = load_create_schema(None)
    routine = registry.content_types["routineVisits"]
    assert routine.commit_ready is False
    assert "choice_options:main.visitType" in routine.missing_contract


def test_explicit_missing_schema_path_is_loud(tmp_path):
    missing = tmp_path / "missing.json"
    try:
        load_create_schema(str(missing))
    except FileNotFoundError as exc:
        assert str(missing) in str(exc)
    else:
        raise AssertionError("missing explicit creation schema must fail")
